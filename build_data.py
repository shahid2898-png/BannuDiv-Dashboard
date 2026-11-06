#!/usr/bin/env python3
"""
build_data.py - builds every monitoring data file of the Bannu Division dashboard from the raw exports.

    python build_data.py RAW_FOLDER OUT_DATA_FOLDER

(run build_coverage.py first / as well for data/coverage.csv - it uses the same RAW folder)

Drop the new round's files in RAW_FOLDER and re-run.  File names it looks for (round label = text after the prefix):

    ICM_<ROUND>.zip                      cluster ICM            -> icm.csv
    ICM_Team_Monitoring_<ROUND>.zip      team monitoring forms  -> teammon.csv + teammon_<ROUND>.csv
    ICM_AIC_Monitoring_<ROUND>.zip       AIC monitoring forms   -> aicmon.csv
    NEAP_<ROUND>.csv                     NEAP                   -> neap.csv
    MPQA_<ROUND>.xls                     MPQA report            -> mpqa.csv
    AICs-UCMOs_Training_<ROUND>.xls      AIC/UCMO training      -> aictrain.csv
    Teams_Training_<ROUND>.xls           team training report   -> teamtrain.csv
    LQAS_<ROUND>.xls                     LQAS report            -> lqas.csv
    PCHS*.xlsx / PCM*.xlsx               post-campaign survey / monitoring (and the PCHS + PCM sheets of the UC-wise workbook) -> pchs.csv, pcm.csv
    Tehsil_File.xlsx                     master list of the division's UCs (District, Tehsil, UC, Ucode)
    *.geojson                            UC-wise and district-wise boundaries -> geo.json

The ROUND in each name is e.g. SNID_May_2026  (label shown in the dashboard: "SNID May 2026").
Only the UCs of Tehsil_File.xlsx are kept.  Monitor codes (M-XXXXXX) are kept; ICM and 0/0 files also carry the monitor name as uploaded.
"""
import os, sys, re, io, glob, json, math, hashlib, base64, zipfile, tempfile, datetime, difflib, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_coverage import DISTRICTS, load_uc_list, stage, fix_month     # noqa: E402

NOW = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
REPORT = []


def say(*a):
    print(*a)


def warn(msg):
    REPORT.append(msg)
    print("  !", msg)


# ------------------------------------------------------------------ helpers
def camp_label(s):
    s = re.sub(r"[_\s]+", " ", str(s)).strip()
    s = re.sub(r"\bSept(?:ember)?\b", "Sep", s, flags=re.I)
    s = re.sub(r"\b(Jan)uary|(Feb)ruary|(Mar)ch|(Apr)il|(Jun)e|(Jul)y|(Aug)ust|(Oct)ober|(Nov)ember|(Dec)ember\b(?=\s*\d{4})",
               lambda m: next(g for g in m.groups() if g), s, flags=re.I)   # 'SNID November 2026' -> 'SNID Nov 2026'
    return fix_month(s)


def label_from_file(path, prefix_re):
    stem = re.sub(r"\.(zip|csv|xls|xlsx)$", "", os.path.basename(path), flags=re.I)
    return camp_label(re.sub(prefix_re, "", stem))


def files(raw, pattern, prefix_re):
    out = {}
    for p in sorted(glob.glob(os.path.join(raw, pattern))):
        out[label_from_file(p, prefix_re)] = p
    return out


def read_html_table(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    t = pd.read_html(io.StringIO(txt), header=None)[0]
    cols = t.columns
    if not all(isinstance(c, (int, np.integer)) for c in cols):        # header cells were read as column labels: make them rows again
        levels = [list(cols.get_level_values(i)) for i in range(cols.nlevels)] if isinstance(cols, pd.MultiIndex) else [list(cols)]
        hdr = pd.DataFrame(levels)
        t.columns = range(t.shape[1])
        hdr.columns = t.columns
        t = pd.concat([hdr, t], ignore_index=True)
    return t


def zip_csv(path, tmp):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        z.extract(names[0], tmp)
        return pd.read_csv(os.path.join(tmp, names[0]), low_memory=False)


def num(s):
    return pd.to_numeric(s, errors="coerce")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def write_csv(df, out, name):
    df = df.copy()
    for c in ("campaign", "camp", "ActivityName"):
        if c in df.columns:
            df[c] = df[c].map(lambda v: fix_month(v) if isinstance(v, str) else v)
    for c in df.columns:             # whole numbers as integers (3 not 3.0)
        if pd.api.types.is_float_dtype(df[c]):
            v = df[c].dropna()
            if len(v) and (v % 1 == 0).all():
                df[c] = df[c].astype("Int64")
    df.to_csv(os.path.join(out, name), index=False)
    open(os.path.join(out, name.replace(".csv", ".updated.txt")), "w").write(NOW)
    say(f"  wrote {name}: {len(df):,} rows")


class UCs:
    """master UC list: code -> canonical (district, tehsil, uc); name matching for reports without a code"""
    def __init__(self, ul):
        self.ul = ul
        self.codes = set(ul.index)
        self.by_name = {}
        for code, r in ul.iterrows():
            self.by_name.setdefault(r["district"], {})[norm(r["uc"])] = code

    def info(self, code):
        r = self.ul.loc[str(int(float(code)))]
        return r["district"], r["tehsil"].upper(), r["uc"].upper()

    def code_of_name(self, district, name):
        d = DISTRICTS.get(str(district).strip().upper(), str(district).strip().upper())
        names = self.by_name.get(d, {})
        k = norm(name)
        if k in names:
            return names[k]
        m = difflib.get_close_matches(k, list(names), n=1, cutoff=0.78)
        return names[m[0]] if m else None


def clean_code(v):
    try:
        return str(int(float(v)))
    except Exception:
        return None


def hav_nearest(df):
    """metres to the nearest other record in df (needs lat/lon columns)"""
    d = df.dropna(subset=["lat", "lon"])
    d = d[(d.lat != 0) & (d.lon != 0)]
    if len(d) < 2:
        return pd.Series(dtype=float)
    la = np.radians(d.lat.values); lo = np.radians(d.lon.values)
    out = np.empty(len(d))
    step = 2500
    for i in range(0, len(d), step):
        a = np.sin((la[i:i + step, None] - la[None, :]) / 2) ** 2 + \
            np.cos(la[i:i + step, None]) * np.cos(la[None, :]) * np.sin((lo[i:i + step, None] - lo[None, :]) / 2) ** 2
        dist = 2 * 6371000 * np.arcsin(np.sqrt(a))
        dist[np.arange(dist.shape[0]), np.arange(i, i + dist.shape[0])] = np.inf
        out[i:i + step] = dist.min(axis=1)
    return pd.Series(out, index=d.index)


def monitor_code(email, name):
    key = (str(email) if pd.notna(email) and str(email).strip() else str(name)).strip().lower()
    h = hashlib.sha256(("bannu|" + key).encode()).digest()
    return "M-" + base64.b32encode(h).decode()[:6]


def tri(v, na_values=()):
    """1 -> '1', 0 -> '0', blank / not-applicable -> '-'"""
    if pd.isna(v) or v in na_values:
        return "-"
    return "1" if v == 1 else "0"


# ------------------------------------------------------------------ ICM (cluster) monitoring
def build_icm(raw, uc, tmp, out):
    rows = []
    for lab, p in [(l, p) for l, p in files(raw, "ICM_*.zip", r"^ICM[_ ]").items() if re.match(r"^(S?NID|Special|KP)\b", l, re.I)]:
        say("ICM", lab)
        d = zip_csv(p, tmp)
        d["code"] = d["fk_uc_id"].map(clean_code)
        d = d[d.code.isin(uc.codes)]
        if "is_archive" in d:
            d = d[num(d.is_archive).fillna(0) == 0]
        for c in ["child_0_11", "child_0_11_vac", "child_12_59", "child_12_59_vac", "team_miss_house", "team_miss_child",
                  "number_no_response_houses", "number_dont_want_participate_houses", "guest", "guest_vac", "total_seen",
                  "total_finger_mark", "rutine_epi", "no_vacc_monitor", "notv_fm", "child_0_59", "child_0_59_vac", "child_away"]:
            d[c] = num(d[c]).fillna(0) if c in d else 0.0
        # newer forms record one 0-59 month figure (no 0-11 / 12-59 split): it goes in the "12-59" slot, as in the Peshawar file
        d["child_12_59"] = d["child_12_59"] + d["child_0_59"]
        d["child_12_59_vac"] = d["child_12_59_vac"] + d["child_0_59_vac"]
        d["latitude"] = num(d.latitude); d["longitude"] = num(d.longitude)
        # missed children by reason, all in CHILDREN so that NT + TVBMC + NA + Refusals = total missed:
        # NA = children away (child_away); refusals = what is left of (checked - vaccinated) after NT, TVBMC and NA
        d["na_ch"] = d["child_away"]
        d["ref_ch"] = ((d.child_12_59 + d.child_0_11) - (d.child_12_59_vac + d.child_0_11_vac)
                       - d.team_miss_house - d.team_miss_child - d.na_ch).clip(lower=0)
        g = d.groupby("unique_id")
        a = g.agg(campaign=("ActivityName", "first"), code=("code", "first"), day=("g1_dayofwork", "first"),
                  designation=("designation_title", "first"), org=("org_name", "first"), pop=("g1_popu_type", "first"),
                  lat=("latitude", "first"), lon=("longitude", "first"), email=("Surveyor Email", "first"),
                  sname=("col_surveyor_name", "first"), mname=("col_surveyor_name", "first"), hh=("hh_no", "size"),
                  a1=("child_0_11", "sum"), a1c=("child_0_11_vac", "sum"), a2=("child_12_59", "sum"), a2c=("child_12_59_vac", "sum"),
                  nt=("team_miss_house", "sum"), tv=("team_miss_child", "sum"),
                  na=("na_ch", "sum"), ref=("ref_ch", "sum"),
                  g=("guest", "sum"), gv=("guest_vac", "sum"), st=("total_seen", "sum"), sv=("total_finger_mark", "sum"),
                  ri=("rutine_epi", "sum"), vm=("no_vacc_monitor", "sum"), ff=("notv_fm", "sum")).reset_index(drop=True)
        a["campaign"] = camp_label(a.campaign.iloc[0]) if len(a) else lab
        a["ma"] = (a.nt > 0).astype(int)
        a["pc"] = (((a.a1 + a.a2) - (a.a1c + a.a2c) > 2) & (a.tv > 0) & (a.nt == 0)).astype(int)   # matches the UC-wise ICM report
        a["mon"] = [monitor_code(e, n) for e, n in zip(a.email, a.sname)]
        a["day"] = num(a.day).fillna(0).astype(int).clip(0, 14).replace(0, "")
        a.loc[(a.lat == 0) | (a.lon == 0), ["lat", "lon"]] = np.nan
        a["sd"] = np.nan
        for mon, grp in a.groupby("mon"):
            s = hav_nearest(grp)
            a.loc[s.index, "sd"] = s
        a["sd"] = a.sd.round().astype("Int64")
        info = a.code.map(lambda c: uc.info(c))
        a["district"] = info.str[0]; a["tehsil"] = info.str[1]; a["uc"] = info.str[2]
        a["lat"] = a.lat.round(3); a["lon"] = a.lon.round(3)
        rows.append(a)
        e = a.a1 + a.a2
        say(f"   clusters {len(a):,}, UCs {a.code.nunique()}, recall {100*(a.a1c+a.a2c).sum()/max(e.sum(),1):.1f}%, "
            f"seen/finger-marked {100*a.sv.sum()/max(a.st.sum(),1):.1f}%")
    if not rows:
        return
    allr = pd.concat(rows, ignore_index=True)
    cols = ["campaign", "district", "tehsil", "uc", "day", "designation", "org", "pop", "lat", "lon", "hh", "a1", "a1c", "a2", "a2c",
            "nt", "tv", "na", "ref", "g", "gv", "st", "sv", "ri", "vm", "ff", "ma", "pc", "mon", "sd", "mname"]
    allr["mname"] = allr.mname.fillna("").astype(str).str.strip()
    allr = allr[cols].sort_values(["campaign", "district", "tehsil", "uc", "day"], kind="stable")
    write_csv(allr, out, "icm.csv")


# ------------------------------------------------------------------ ICM catch-up clusters: 0/0 and locked houses
def build_zerolock(raw, uc, tmp, out):
    """One row per (catch-up cluster, house type). hh_type 1 = 0/0 house, 2 = locked house; finding 1 = matched, 0 = not matched."""
    rows = []
    for lab, p in files(raw, "ICM_Catchup_Clusters_*.zip", r"^ICM[_ ]Catchup[_ ]Clusters[_ ]").items():
        say("Catch-up clusters", lab)
        d = zip_csv(p, tmp)
        d["code"] = d["fk_uc_id"].map(clean_code)
        d = d[d.code.isin(uc.codes)]
        if "is_archive" in d:
            d = d[num(d.is_archive).fillna(0) == 0]
        d["ht"] = num(d.hh_type); d["fi"] = num(d.finding)
        d = d[d.ht.isin([1, 2]) & d.fi.isin([0, 1])]          # rows with a house and a finding
        if d.empty:
            warn(f"Catch-up clusters {lab}: no 0/0 or locked houses for the division's UCs"); continue
        d["nm"] = (d.fi == 0).astype(int); d["mt"] = 1 - d.nm
        d["vac"] = num(d.f_vaccinated).fillna(0) * d.nm
        d["unv"] = num(d.f_un_vaccinated).fillna(0) * d.nm
        d["mon"] = [monitor_code(e, n) for e, n in zip(d["Surveyor Email"], d.col_surveyor_name)]
        d["day"] = num(d.g1_dayofwork).fillna(0).astype(int).clip(0, 14).replace(0, "")
        g = d.groupby(["unique_id", "ht"], sort=False)
        a = g.agg(mname=("col_surveyor_name", "first"), code=("code", "first"), day=("day", "first"), mon=("mon", "first"), desg=("designation_title", "first"),
                  hh=("ht", "size"), mt=("mt", "sum"), nm=("nm", "sum"), vac=("vac", "sum"), unv=("unv", "sum")).reset_index()
        a["cid"] = pd.factorize(a.unique_id)[0] + 1
        info = a.code.map(lambda c: uc.info(c))
        a["camp"] = camp_label(d.ActivityName.iloc[0]) if len(d) else lab
        a["district"] = info.str[0]; a["tehsil"] = info.str[1]; a["uc"] = info.str[2]
        a["type"] = a.ht.map({1: "0/0", 2: "Locked"})
        rows.append(a)
        say(f"   {a.cid.nunique():,} clusters, {int(a.hh.sum()):,} houses, {int(a.nm.sum())} not matched, {a.code.nunique()} UCs")
    if rows:
        r = pd.concat(rows, ignore_index=True)
        r["cid"] = r.camp + "-" + r.cid.astype(str)
        r = r[["camp", "cid", "district", "tehsil", "uc", "day", "mon", "desg", "type", "hh", "mt", "nm", "vac", "unv", "mname"]]
        r = r.sort_values(["camp", "district", "tehsil", "uc", "cid"], kind="stable")
        write_csv(r, out, "zerolock.csv")


# ------------------------------------------------------------------ team monitoring
TM_MAP = [  # position -> raw column(s)
    "g1_team_composition_female", "g1_team_composition_adult", "g1_NamesMP_match", ("g1_valid_marker", "g1_quality_marker"),
    "g1_T_memTrained", "@neoc", "@lid", "@water", "g1_opv_dry_valid_vvm", "g1_opv_dry_dry", "g1_opv_dry_intact",
    "g1_opv_dry_re_capped", "g1_matched_admin_team", ("g1_t_sheetcorrect", "g1_t_sheet_accurate"), "@vita",
    "g1_T_recordedMissed", "g1_T_mapAvailable", "g1_Tworkload", "g2_obsorve", "g2_T_chiledAwayHome", "g2_T_rmAFP",
    "g2_T_markerUsed", "g2_T_CfMarked", "g2_T_childVaccinatedToday"]


def team_ind(d):
    cols = []
    cc = d["cold_chain"].astype("string") if "cold_chain" in d else pd.Series(pd.NA, index=d.index, dtype="string")
    for item in TM_MAP:
        if item == "@neoc":
            s = cc.map(lambda v: "-" if pd.isna(v) else ("1" if "NEOC approved" in v else "0"))
        elif item == "@lid":
            s = cc.map(lambda v: "-" if pd.isna(v) else ("1" if "Lid and seal" in v else "0"))
        elif item == "@water":
            s = cc.map(lambda v: "-" if pd.isna(v) else ("1" if "No water inside" in v else "0"))
        elif item == "@vita":
            s = num(d["g1_vitamin_a"]).map(lambda v: tri(v, na_values=(2,)))
        else:
            names = item if isinstance(item, tuple) else (item,)
            v = pd.Series(np.nan, index=d.index)
            for n in names:
                if n in d:
                    v = v.combine_first(num(d[n]))
            s = v.map(tri)
        cols.append(s.astype(str))
    out = cols[0]
    for c in cols[1:]:
        out = out + c
    return out


def build_team(raw, uc, tmp, out):
    index = []
    for lab, p in files(raw, "ICM_Team_Monitoring_*.zip", r"^ICM[_ ]Team[_ ]Monitoring[_ ]").items():
        say("Team monitoring", lab)
        d = zip_csv(p, tmp)
        d["code"] = d["fk_uc_id"].map(clean_code)
        d = d[d.code.isin(uc.codes)].drop_duplicates("unique_id", keep="last")
        if d.empty:
            warn(f"team monitoring {lab}: no forms for the division's UCs")
            continue
        camp = camp_label(d.ActivityName.iloc[0])
        d["lat"] = num(d.latitude); d["lon"] = num(d.longitude)
        d.loc[(d.lat == 0) | (d.lon == 0), ["lat", "lon"]] = np.nan
        d["mon"] = [monitor_code(e, n) for e, n in zip(d["Surveyor Email"], d["surveyor_name"])]
        d["sd"] = np.nan
        for _, grp in d.groupby("mon"):
            s = hav_nearest(grp.rename(columns={}))
            d.loc[s.index, "sd"] = s
        d["ind"] = team_ind(d)
        dt = pd.to_datetime(d.date_created.astype(str).str.replace(r"\s+", " ", regex=True), format="%d-%b-%Y %H:%M:%S", errors="coerce")
        d["hm"] = dt.dt.strftime("%m%d%H%M").fillna("")
        ucs = sorted(set(d.code))
        uidx = {c: i + 1 for i, c in enumerate(ucs)}
        orgs = sorted(set(d.org_name.dropna())); oidx = {c: i + 1 for i, c in enumerate(orgs)}
        dess = sorted(set(d.designation_title.dropna())); gidx = {c: i + 1 for i, c in enumerate(dess)}
        mons = sorted(set(d.mon)); midx = {c: i + 1 for i, c in enumerate(mons)}
        fname = "teammon_" + re.sub(r"\s+", "_", lab) + ".csv"
        lines = []
        for c in ucs:
            dist, teh, u = uc.info(c)
            lines.append(f"U,{uidx[c]},{dist},{teh},{u}")
        lines += [f"G,{i},{n}" for n, i in gidx.items()]
        lines += [f"O,{i},{n}" for n, i in oidx.items()]
        lines += [f"M,{i},{n}" for n, i in midx.items()]
        lines.append("day,u,o,g,m,aic,team,lat,lon,sd,hm,ind")
        day = num(d.g1_day).fillna(0).astype(int)
        aic = num(d.aic_number).map(lambda v: "" if pd.isna(v) else str(int(v)))
        team = num(d.g1_team_no).map(lambda v: "" if pd.isna(v) else str(int(v)))
        d = d.sort_values(["code", "g1_day"], kind="stable")
        for i, r in d.iterrows():
            lines.append(",".join([
                str(day[i]) if 1 <= day[i] <= 14 else "", str(uidx[r.code]), str(oidx.get(r.org_name, "")), str(gidx.get(r.designation_title, "")),
                str(midx[r.mon]), aic[i], team[i],
                "" if pd.isna(r.lat) else f"{r.lat:.3f}", "" if pd.isna(r.lon) else f"{r.lon:.3f}",
                "" if pd.isna(r.sd) else str(int(round(r.sd))), r.hm, r.ind]))
        with open(os.path.join(out, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        index.append((camp, fname, len(d)))
        say(f"   {camp}: {len(d):,} forms, {len(ucs)} UCs, {len(mons)} monitors -> {fname}")
    if index:
        old = []
        idxp = os.path.join(out, "teammon.csv")
        if os.path.exists(idxp):
            old = [tuple(r.split(",", 2)) for r in open(idxp).read().splitlines()[1:] if r.strip()]
        keep = {c: (c, f, n) for c, f, n in old if os.path.exists(os.path.join(out, f))}
        for c, f, n in index:
            keep[c] = (c, f, str(n))
        pd.DataFrame(list(keep.values()), columns=["camp", "file", "forms"]).to_csv(idxp, index=False)
        open(os.path.join(out, "teammon.updated.txt"), "w").write(NOW)


# ------------------------------------------------------------------ AIC monitoring
def aic_ind(d):
    def col(n):
        return num(d[n]) if n in d else pd.Series(np.nan, index=d.index)
    parts = [
        col("g1_sup_trained_count").map(tri),
        col("is_vacc_carrier").map(tri), col("is_opv_vials").map(tri), col("is_finger_marker").map(tri),
        col("is_coldchain_within_vacc_cntr").map(tri), col("is_microplan_u_copy").map(tri),
        col("is_vit_a_and_scissors").map(lambda v: tri(v, (2,))), col("g_name_match").map(tri),
        col("g_ipc_team").map(lambda v: "-" if pd.isna(v) else ("1" if v in (1, 2) else "0")),
        col("g_ipc_community").map(lambda v: "-" if pd.isna(v) else ("1" if v in (1, 2) else "0")),
        col("g1_sup_suprvsn").map(lambda v: tri(v, (2,))), col("g1_Comp_tm_hhform").map(tri),
        col("g1_hrp_present").map(lambda v: tri(v, (2,))),
        col("g_ipc_team").map(lambda v: "-" if pd.isna(v) else ("1" if v == 2 else "0")),
        col("g_ipc_community").map(lambda v: "-" if pd.isna(v) else ("1" if v == 2 else "0")),
    ]
    out = parts[0].astype(str)
    for p in parts[1:]:
        out = out + p.astype(str)
    return out


def build_aicmon(raw, uc, tmp, out):
    rows = []
    for lab, p in files(raw, "ICM_AIC_Monitoring_*.zip", r"^ICM[_ ]AIC[_ ]Monitoring[_ ]").items():
        say("AIC monitoring", lab)
        d = zip_csv(p, tmp)
        d["code"] = d["fk_uc_id"].map(clean_code)
        d = d[d.code.isin(uc.codes)]
        if "is_archive" in d:
            d = d[num(d.is_archive).fillna(0) == 0]
        d = d.drop_duplicates("unique_id", keep="last")
        if d.empty:
            warn(f"AIC monitoring {lab}: no forms for the division's UCs"); continue
        info = d.code.map(lambda c: uc.info(c))
        day = num(d.g1_Cday).fillna(0).astype(int)
        area = num(d.area_number).map(lambda v: "" if pd.isna(v) else str(int(v)))
        r = pd.DataFrame({"camp": camp_label(d.ActivityName.iloc[0]), "day": day.where((day >= 1) & (day <= 14), ""),
                          "district": info.str[0], "tehsil": info.str[1], "uc": info.str[2], "org": d.org_name,
                          "desg": d.designation_title, "area": area, "ind": aic_ind(d)})
        rows.append(r)
        say(f"   {r.camp.iloc[0]}: {len(r):,} forms, {d.code.nunique()} UCs")
    if rows:
        write_csv(pd.concat(rows, ignore_index=True), out, "aicmon.csv")


# ------------------------------------------------------------------ NEAP
NEAP_MAP = {"mt": "mobileTeams", "ft": "fixedTeams", "tp": "TransitPoints", "aic": "areaIcs", "aicF": "aicFemale", "aicG": "aicGovt",
            "aicT": "aicTrained", "zs": "zonalSupervisor", "hh": "HH059_MP", "sch": "School_MP", "fix": "Fixed_MP", "trn": "Transit_MP",
            "m18": "alltm18yr", "gov": "govtWorker", "loc": "mtLocalmem", "fem": "mtFemale", "tmT": "alltmTrg", "upec": "UpecHeld",
            "ucmo": "ucmo", "ucsec": "ucSecratry", "sho": "shoUpec", "inCh": "inaccCh", "inAr": "areasInacc",
            "dr1": "MPDR_Passed", "dr2": "MPDR_Int", "dr3": "MPDR_Failed", "fv1": "MPFV_Passed", "fv2": "MPFV_Int", "fv3": "MPFV_Failed",
            "stall": "stall_method_trg", "hml": "no_of_hmlt", "newA": "new_areas", "walk": "team_walkthru", "hrm": "house_rmap",
            "hrmp": "HRMP", "zd": "Dose0Cov", "vca": "vacc_carr_avbl", "vcr": "vacc_carr_req"}


def fmt_dt(s, with_time=True):
    t = pd.to_datetime(s.astype(str), dayfirst=True, errors="coerce")
    return t.dt.strftime("%Y-%m-%d %H:%M").fillna("")


def build_neap(raw, uc, out):
    rows = []
    for lab, p in files(raw, "NEAP_*.csv", r"^NEAP[_ ]").items():
        say("NEAP", lab)
        n = pd.read_csv(p, low_memory=False)
        n["code"] = n["Ucode"].map(clean_code)
        n = n[n.code.isin(uc.codes)]
        n["_ts"] = pd.to_datetime(n.TimeStamp, dayfirst=True, errors="coerce")
        n = n.sort_values("_ts").drop_duplicates("code", keep="last")
        info = n.code.map(lambda c: uc.info(c))
        o = pd.DataFrame({"camp": camp_label(n.ActivityName.iloc[0]) if len(n) else lab, "ts": fmt_dt(n.TimeStamp),
                          "dist": info.str[0], "teh": info.str[1], "uc": info.str[2], "ucode": n.code, "tier": num(n.Tier).astype("Int64"),
                          "cat": n.Category, "upecdate": fmt_dt(n.UpecDate.replace("0000-00-00", np.nan))})
        for k, v in NEAP_MAP.items():
            o[k] = num(n[v]) if v in n else np.nan
        o["mpuc"] = (o.hh.fillna(0) > 0).astype(int)
        order = ["camp", "ts", "dist", "teh", "uc", "ucode", "tier", "cat", "upecdate", "mt", "ft", "tp", "aic", "aicF", "aicG", "aicT", "zs", "hh",
                 "sch", "fix", "trn", "m18", "gov", "loc", "fem", "tmT", "upec", "ucmo", "ucsec", "sho", "inCh", "inAr", "dr1", "dr2", "dr3",
                 "fv1", "fv2", "fv3", "mpuc", "stall", "hml", "newA", "walk", "hrm", "hrmp", "zd", "vca", "vcr"]
        o = o[order]
        missing = sorted(set(uc.codes) - set(o.ucode))
        say(f"   {len(o)} UCs, NEAP target {int(o.hh.sum()):,}" + (f"; {len(missing)} UCs without a NEAP row" if missing else ""))
        if missing:
            warn(f"NEAP {lab}: no row for UC codes {missing}")
        rows.append(o)
    if rows:
        write_csv(pd.concat(rows, ignore_index=True).sort_values(["camp", "dist", "teh", "uc"], kind="stable"), out, "neap.csv")


# ------------------------------------------------------------------ MPQA
MPQA_SEP_Q = ["Micro Plan available with UCMO", "HR numbers match District Resource Plan", "SBC component complete", "UC Map complete",
              "HH totals reconcile", "Team workload within limits", "Hamlets / streets listed", "AIC/AS Map complete",
              "Vaccine & logistics plan", "MMP in AIC/AS area", "Starting Point HH details", "Team Route Map & landmark",
              "School / madrassa target", "Hamlet exists in Operational Plan", "MMP in today's plan", "Ending Point HH details",
              "Geographic category matches"]


def build_mpqa(raw, uc, out):
    rows, qcols = [], []
    for lab, p in files(raw, "MPQA_*.xls", r"^MPQA[_ ]").items():
        say("MPQA", lab)
        x = read_html_table(p)
        isnum = x.iloc[:, 4].map(lambda v: clean_code(v) is not None)
        first_data = int(isnum.idxmax())
        dcols = x.shape[1]
        qn = []
        for j in range(9, dcols):
            parts = []
            for k in range(1, first_data):
                t = str(x.iloc[k, j]).strip()
                if t and t != "%" and t.lower() != "nan" and t not in parts:
                    parts.append(t)
            grp = str(x.iloc[0, j]).strip().lower()
            qn.append(("Desk: " if grp.startswith("desk") else "Field: " if grp.startswith("field") else "") + " - ".join(parts)[:90])
        if lab.lower().endswith("sep 2026") and len(qn) == len(MPQA_SEP_Q):
            qn = list(MPQA_SEP_Q)
        else:
            qn = [f"{q} [{lab}]" for q in qn]
        data = x.iloc[first_data:].copy()
        data = data[data.iloc[:, 4].map(lambda v: clean_code(v) is not None)]
        data["code"] = data.iloc[:, 4].map(clean_code)
        data = data[data.code.isin(uc.codes)]
        info = data.code.map(lambda c: uc.info(c))
        o = pd.DataFrame({"Campaign": lab, "Province": "KP", "District": info.str[0], "Tehsil": info.str[1], "UC name": info.str[2],
                          "UC Code": data.code, "MPQA Reported": data.iloc[:, 5].astype(str).str.strip().str.title()})
        desk, field, overall = num(data.iloc[:, 6]), num(data.iloc[:, 7]), num(data.iloc[:, 8])
        o["Desk Review %"], o["Field Validation %"], o["Over-all %"] = desk, field, overall
        o["Band"] = np.where(overall.isna(), "", np.where(overall >= 90, "Good", np.where(overall >= 70, "Intermediate", "Failed")))
        o["Field vs Desk"] = (field - desk).round(2)
        for j, q in enumerate(qn):
            o[q] = num(data.iloc[:, 9 + j])
            if q not in qcols:
                qcols.append(q)
        missing = sorted(set(uc.codes) - set(o["UC Code"]))
        say(f"   {len(o)} UCs ({(o['MPQA Reported']=='Yes').sum()} reported); {len(missing)} UCs of the division not in the MPQA file")
        rows.append(o)
    wb = os.path.join(raw, "UC Wise Data Sets.xlsx")
    if not os.path.exists(wb):
        wb = os.path.join(raw, "UC_Wise_Data_Sets.xlsx")
    if os.path.exists(wb) and "MPQA" in pd.ExcelFile(wb).sheet_names:        # earlier rounds that exist only as UC-level summary in the workbook
        w = pd.read_excel(wb, "MPQA")
        w["Campaign"] = w["Campaign"].astype(str).str.strip().str.replace(r"\b(\d\d)$", lambda m: "20" + m.group(1), regex=True).map(camp_label)
        have = {r["Campaign"].iloc[0] for r in rows}
        w = w[~w["Campaign"].isin(have)]
        w["code"] = w["UC Code"].map(clean_code); w = w[w.code.isin(uc.codes)]
        for lab, g in w.groupby("Campaign"):
            info = g.code.map(lambda c: uc.info(c))
            o = pd.DataFrame({"Campaign": lab, "Province": "KP", "District": info.str[0], "Tehsil": info.str[1], "UC name": info.str[2],
                              "UC Code": g.code, "MPQA Reported": g["MPQA Reported"].astype(str).str.strip().str.title(),
                              "Desk Review %": num(g["Desk Review %"]), "Field Validation %": num(g["Field Validation %"]), "Over-all %": num(g["Over-all %"])})
            ov = o["Over-all %"]
            o["Band"] = np.where(ov.isna(), "", np.where(ov >= 90, "Good", np.where(ov >= 70, "Intermediate", "Failed")))
            o["Field vs Desk"] = (o["Field Validation %"] - o["Desk Review %"]).round(2)
            say(f"MPQA {lab}: {len(o)} UCs from the workbook (summary only)")
            rows.append(o)
    if rows:
        allr = pd.concat(rows, ignore_index=True)
        base = ["Campaign", "Province", "District", "Tehsil", "UC name", "UC Code", "MPQA Reported", "Desk Review %", "Field Validation %",
                "Over-all %", "Band", "Field vs Desk"]
        write_csv(allr[base + qcols], out, "mpqa.csv")


def training_zips(raw, pattern, prefix_re, uc, what):
    """raw form-data exports (zip with one csv, e.g. AIC_-_UCMO_Training_Monitoring_V2-72_combined_*.csv): returns {round label: DataFrame}
    limited to the division's districts, archived forms dropped, 'ucs' reduced to the division's canonical UC names (':' separated)."""
    out = {}
    for lab, p in files(raw, pattern, prefix_re).items():
        with tempfile.TemporaryDirectory() as tmp:
            df = zip_csv(p, tmp)
        n0 = len(df)
        df = df[df["district_name"].astype(str).str.strip().str.upper().isin(DISTRICTS)].copy()
        if "is_archive" in df:
            df = df[pd.to_numeric(df["is_archive"], errors="coerce").fillna(0) == 0]
        keep, ucs, tehs = [], [], []
        for i, r in df.iterrows():
            dist = DISTRICTS[str(r["district_name"]).strip().upper()]
            names = [n.strip() for n in re.split(r"[:;]", str(r["ucs"])) if n.strip()]
            canon, c0 = [], None
            for n in names:
                c = uc.code_of_name(dist, n)
                if c:
                    canon.append(uc.info(c)[2]); c0 = c0 or c
                else:
                    warn(f"{what} {lab}: UC '{n}' ({dist}) not found in the UC list")
            if canon:
                keep.append(i); ucs.append(":".join(canon)); tehs.append(uc.info(c0)[1])
        df = df.loc[keep].copy()
        df["_dist"] = [DISTRICTS[str(x).strip().upper()] for x in df["district_name"]]
        df["_ucs"], df["_teh"] = ucs, tehs
        say(f"{what} {lab}: {n0} forms in the file, {len(df)} in the division's UCs")
        out[lab] = df
    return out


# ------------------------------------------------------------------ AIC / UCMO training
def yes(v):
    s = str(v).strip().lower()
    if s in ("yes", "y", "1", "1.0", "true"): return 1
    if s in ("no", "n", "0", "0.0", "false"): return 0
    return np.nan


AIC_RAW_Q = ["tmat_agenda", "Res_Mat", "blank_form", "tmar_tsheet", "tmar_vaccvial", "tmar_rmap", "tmar_fm", "tmar_chalk", "tmar_other",
             "p_intro", "p_briefed", "mp_obsorved", "mp_briefed", "new_mp_updated", "mp_desk_field", "flw_obsorved", "flw_agenda", "flw_practical",
             "com_sm_obsorved", "com_sm_review", "com_sm_mapping", "com_sm_updted", "local_lang", "brief_guidline", "qa", "t_venue"]


def aictrain_from_forms(raw, uc):
    rows, done = [], set()
    for lab, df in training_zips(raw, "AICs-UCMOs_Training_*.zip", r"^AICs[-_ ]UCMOs[_ ]Training[_ ]", uc, "AIC/UCMO training").items():
        done.add(lab)
        for _, r in df.iterrows():
            q = [yes(r.get(c)) for c in AIC_RAW_Q]                      # q01-q26 (material ... venue)
            dur = num(pd.Series([r.get("duration")])).iloc[0]
            q += [(1 if dur >= 240 else 0) if pd.notna(dur) else np.nan, (1 if 240 <= dur <= 300 else 0) if pd.notna(dur) else np.nan]   # q27, q28
            q.append(yes(r.get("def_training")))                        # q29 defaulters' training date decided
            fb = str(r.get("feedback")).strip().lower()
            q.append(np.nan if fb in ("", "nan") else (0 if fb in ("no", "nil", "nill", "none", "n/a", "na", "-", "0") else 1))   # q30
            q.append(yes(r.get("else_monit")))                          # q31 also monitored by someone else
            pic = str(r.get("autmv2_picture")).strip()
            q.append(1 if pic and pic.lower() != "nan" else 0)          # q32 live picture uploaded
            ex, m, f, new = (num(pd.Series([r.get(c)])).iloc[0] for c in ("no_partcpnt", "attend_m", "attend_f", "new_partcpnt"))
            att = (m if pd.notna(m) else 0) + (f if pd.notna(f) else 0)
            sc = [v for v in q if pd.notna(v)]
            rec = {"camp": lab, "dist": r["_dist"], "teh": r["_teh"], "uc": r["_ucs"], "type": str(r.get("training_type")).strip().upper(),
                   "day": int(num(pd.Series([r.get("training_day")])).fillna(1).iloc[0]), "exp": ex, "m": m, "f": f, "att": att, "new": new, "dur": dur,
                   "overall": round(sum(sc) / len(sc), 5) if sc else np.nan, "attpct": round(att / ex, 5) if ex else np.nan}
            for i, v in enumerate(q):
                rec[f"q{i+1:02d}"] = v
            rows.append(rec)
    return rows, done


def build_aictrain(raw, uc, out):
    rows, done = aictrain_from_forms(raw, uc)
    for lab, p in files(raw, "AICs-UCMOs_Training_*.xls", r"^AICs[-_ ]UCMOs[_ ]Training[_ ]").items():
        if lab in done:
            continue
        say("AIC/UCMO training", lab)
        x = read_html_table(p)
        x = x[x.iloc[:, 0].astype(str).str.strip().str.fullmatch(r"\d+")]
        for _, r in x.iterrows():
            dist = DISTRICTS.get(str(r[4]).strip().upper(), str(r[4]).strip().upper())
            names = [n.strip() for n in re.split(r"[:;]", str(r[6])) if n.strip()]
            canon = []
            for n in names:
                c = uc.code_of_name(dist, n)
                if c: canon.append(uc.info(c)[2])
                else: warn(f"AIC training {lab}: UC '{n}' ({dist}) not found in the UC list")
            if not canon:
                continue
            c0 = uc.code_of_name(dist, names[0])
            q = [yes(r[26 + i]) for i in range(22)]           # q01-q22: material .. social mobilisation
            q += [yes(r[48]), yes(r[49]), yes(r[51]), yes(r[50])]   # q23 language, q24 guidelines, q25 questions answered, q26 venue
            dur = num(pd.Series([r[21]])).iloc[0]
            q += [(1 if dur >= 240 else 0) if pd.notna(dur) else np.nan, (1 if 240 <= dur <= 300 else 0) if pd.notna(dur) else np.nan]
            q.append(yes(r[11]))                                  # q29 defaulters' training date decided
            fb = str(r[53]).strip().lower()
            q.append(np.nan if fb in ("", "nan") else (0 if fb in ("no", "nil", "none", "n/a", "na", "-", "0") else 1))   # q30 checklist/feedback
            q.append(yes(r[19]))                                  # q31 also monitored by someone else
            q.append(np.nan)                                      # q32 live picture (not in the export)
            ex, m, f, new = (num(pd.Series([r[i]])).iloc[0] for i in (22, 23, 24, 25))
            att = (m if pd.notna(m) else 0) + (f if pd.notna(f) else 0)
            sc = [v for v in q if pd.notna(v)]
            rec = {"camp": camp_label(r[1]), "dist": dist, "teh": uc.info(c0)[1] if c0 else str(r[5]).upper(), "uc": ":".join(canon),
                   "type": str(r[10]).strip().upper(), "day": int(num(pd.Series([r[9]])).fillna(1).iloc[0]), "exp": ex, "m": m, "f": f,
                   "att": att, "new": new, "dur": dur, "overall": round(sum(sc) / len(sc), 5) if sc else np.nan,
                   "attpct": round(att / ex, 5) if ex else np.nan}
            for i, v in enumerate(q):
                rec[f"q{i+1:02d}"] = v
            rows.append(rec)
        say(f"   {sum(1 for r in rows if r['camp'] == camp_label(x.iloc[0, 1]))} sessions")
    if rows:
        df = pd.DataFrame(rows)
        df["camp"] = df["camp"].map(camp_label)
        write_csv(df, out, "aictrain.csv")


# ------------------------------------------------------------------ Teams training (UC-level report)
TT_SEP = [("training venue", "t_venue"), ("training agenda", "tmat_agenda"), ("tally sheet", "tmar_tsheet"), ("vaccine vials", "tmar_vaccvial"),
          ("route map", "tmar_rmap"), ("finger marker", "tmar_fm"), ("chalk", "tmar_chalk"), ("other training items", "tmar_other"),
          ("polio introduction", "p_briefed"), ("briefing on polio", "p_briefed"), ("aic sitting", "aic_sit"), ("video shared", "vacc_admin_videos"),
          ("training session method", "stall_method"), ("ability to answer", "qa"), ("water facility", "water_avail"),
          ("functional washroom", "func_wash"), ("stall member and community", "stall_comm"), ("stall member visiting", "stall_visiting"),
          ("stall keeper providing", "stall_guid"), ("vaccine vail", "vacc_mang_vvm"), ("vaccination process", "stall_visiting_process"),
          ("perfoming finger", "stall_pfm"), ("provide guideline for finger", "stall_guid_fm"), ("stall scenarios writing", "stall_board"),
          ("stall scenarios for door", "stall_doorm"), ("provide guideline for door", "stall_guid_door"),
          ("stall scenarios for tally", "stall_tely_pana"), ("stall tally sheet", "stall_fill_telly"), ("provide guideline for tally", "stall_guid_data_r"),
          ("asked about afp", "stall_afp"), ("ddm payment", "stall_payment_p"), ("training language", "local_lang"),
          ("training guideline and briefing", "local_lang_guideline")]
TT_STALL = {"ipc": "ipc", "vaccine administration": "vacc_administration", "finger marking": "finger_mark", "door chalking": "door_chalk",
            "data recording": "data_record", "gender mainstreaming": "gender_mainstream"}
TT_COLS = (["t_venue", "water_avail", "func_wash", "tmat_agenda", "tmar_tsheet", "tmar_vaccvial", "tmar_rmap", "tmar_fm", "tmar_chalk", "tmar_other",
            "p_briefed", "aic_sit", "vacc_admin_videos", "stall_method"] + ["sk_" + v for v in TT_STALL.values()] + ["sop_" + v for v in TT_STALL.values()] +
           ["qa", "feedback", "ttmv2_picture", "stall_comm", "stall_visiting", "stall_guid", "vacc_mang_vvm", "stall_visiting_process", "stall_pfm",
            "stall_guid_fm", "stall_board", "stall_doorm", "stall_guid_door", "stall_tely_pana", "stall_fill_telly", "stall_guid_data_r", "stall_afp",
            "stall_payment_p", "local_lang", "local_lang_guideline", "field_activity", "field_demonstration"])


def teamtrain_from_forms(raw, uc):
    """raw team-training form export -> same columns as teamtrain.csv (the dashboard reads the form fields directly)"""
    rows, done = [], set()
    for lab, df in training_zips(raw, "Teams_Training_*.zip", r"^Teams?[_ ]Training[_ ]", uc, "Team training").items():
        done.add(lab)
        for _, r in df.iterrows():
            rec = {"ActivityName": lab, "district_name": r["_dist"], "tehsil_name": r["_teh"], "ucs": r["_ucs"],
                   "training_type": (str(r.get("training_type")).strip().lower() if pd.notna(r.get("training_type")) else "mt"),
                   "no_partcpnt": r.get("no_partcpnt"), "attend_m": r.get("attend_m"), "attend_f": r.get("attend_f"),
                   "new_partcpnt": r.get("new_partcpnt"), "duration": r.get("duration")}
            for k in TT_COLS:
                v = r.get(k)
                if k == "ttmv2_picture":
                    v = 1 if pd.notna(v) and str(v).strip() else ""
                elif k == "feedback":
                    t = str(v).strip().lower()
                    v = "" if t in ("", "nan") else (0 if t in ("no", "nil", "nill", "none", "n/a", "na", "-", "0") else 1)
                else:
                    y = yes(v)
                    v = "" if pd.isna(y) else y
                rec[k] = v
            rows.append(rec)
    return rows, done


def build_teamtrain(raw, uc, out):
    rows, done = teamtrain_from_forms(raw, uc)
    for lab, p in files(raw, "Teams_Training_*.xls", r"^Teams?[_ ]Training[_ ]").items():
        if lab in done:
            continue
        say("Team training", lab)
        x = read_html_table(p)
        hr = x.index[x.iloc[:, 0].astype(str).str.strip() == "Sr."]
        h0 = x.iloc[hr[0]].astype(str).tolist(); h1 = x.iloc[hr[0] + 1].astype(str).tolist() if (len(hr) and hr[0] + 1 < len(x) and not str(x.iloc[hr[0] + 1, 0]).strip().isdigit()) else h0
        data = x[x.iloc[:, 0].astype(str).str.strip().str.fullmatch(r"\d+")]
        colmap, seen = {}, set()
        for i in range(x.shape[1]):
            g, s = h0[i].strip().lower(), h1[i].strip().lower()
            key = None
            if "established" in g and s in TT_STALL:
                key = "sk_" + TT_STALL[s]
            elif "as per sop" in g and s in TT_STALL:
                key = "sop_" + TT_STALL[s]
            else:
                for pat, k in TT_SEP:
                    if pat in s or pat in g:
                        key = k; break
            if key and key not in seen and i >= 12:
                colmap[key] = i; seen.add(key)
        new_form = "sk_ipc" in colmap
        say(f"   {len(data)} UC rows; {len(colmap)} checklist columns mapped ({'new' if new_form else 'older'} form)")
        for _, r in data.iterrows():
            dist = DISTRICTS.get(str(r[4]).strip().upper(), str(r[4]).strip().upper())
            codes = []
            for nm in [n.strip() for n in re.split(r"[:;]", str(r[6])) if n.strip()]:
                cc = uc.code_of_name(dist, nm)
                if cc: codes.append(cc)
                else: warn(f"Team training {lab}: UC '{nm}' ({dist}) not found in the UC list")
            if not codes:
                continue
            d, t, _ = uc.info(codes[0])
            u = ":".join(uc.info(cc)[2] for cc in codes)
            tot = num(pd.Series([r[9]])).iloc[0]; fem = num(pd.Series([r[10]])).iloc[0]
            if new_form:      # Sep format: total (incl. female), female, first time
                am, af, nw = tot, fem, num(pd.Series([r[11]])).iloc[0]
            else:             # older format: male, female, fresh
                am, af, nw = tot, fem, num(pd.Series([r[11]])).iloc[0]
            rec = {"ActivityName": camp_label(r[1]), "district_name": d, "tehsil_name": t, "ucs": u, "training_type": "mt",
                   "no_partcpnt": num(pd.Series([r[8]])).iloc[0], "attend_m": am, "attend_f": af, "new_partcpnt": nw, "duration": ""}
            for k in TT_COLS:
                if k in colmap:
                    v = num(pd.Series([r[colmap[k]]])).iloc[0]
                    rec[k] = "" if pd.isna(v) else round(v / 100, 4)
                else:
                    rec[k] = ""
            rows.append(rec)
    if rows:
        write_csv(pd.DataFrame(rows), out, "teamtrain.csv")


# ------------------------------------------------------------------ LQAS
LQ_COLS = ["camp", "month", "year", "district", "tehsil", "uc", "code", "type", "result", "lots", "away", "newborn", "migrant", "refused",
           "sleeping", "tnv", "tmc", "vnfm", "unknown", "inacc", "missed", "r_demand", "r_fatigue", "r_hospdr", "r_religion", "r_rumour",
           "r_security", "r_sick", "r_unknown", "d0", "d13", "d4p", "ddk"]


def build_lqas_workbook(raw, uc, out):
    """eLQAS sheet of 'UC Wise Data Sets.xlsx' (same layout as the Peshawar LQAS file, all rounds). Returns True if used."""
    wb = os.path.join(raw, "UC Wise Data Sets.xlsx")
    if not os.path.exists(wb):
        wb = os.path.join(raw, "UC_Wise_Data_Sets.xlsx")
    if not os.path.exists(wb) or "eLQAS" not in pd.ExcelFile(wb).sheet_names:
        return False
    x = pd.read_excel(wb, "eLQAS", header=None).iloc[2:]
    x = x[x[8].map(lambda v: clean_code(v) is not None)]
    rows = []
    N = lambda v: "" if pd.isna(v) else int(v)
    for _, r in x.iterrows():
        c = clean_code(r[8])
        if c not in uc.codes:
            continue
        d, t, u = uc.info(c)
        res = str(r[10]).strip()
        rec = dict.fromkeys(LQ_COLS, "")
        rec.update({"camp": camp_label(str(r[0]).replace("Apr 26", "Apr 2026")), "month": str(r[1]).upper(), "year": N(num(pd.Series([r[2]])).iloc[0]), "district": d, "tehsil": t,
                    "uc": u, "code": c, "type": "CBV" if str(r[9]).upper().startswith("CBV") else "NON-CBV",
                    "result": "Pass" if res.lower().startswith("pass") else "Fail" if res.lower().startswith("fail") else res, "lots": N(r[11]) or 1,
                    "away": N(r[12]), "newborn": N(r[14]), "migrant": N(r[16]), "refused": N(r[18]), "sleeping": N(r[20]), "tnv": N(r[22]),
                    "tmc": N(r[24]), "vnfm": N(r[26]), "unknown": N(r[28]), "inacc": N(r[30]), "missed": N(r[32])})
        for k, i in zip(["r_demand", "r_fatigue", "r_hospdr", "r_religion", "r_rumour", "r_security", "r_sick", "r_unknown", "d0", "d13", "d4p", "ddk"],
                        list(range(33, 41)) + [42, 43, 44, 45]):
            rec[k] = N(num(pd.Series([r[i]])).iloc[0])
        rows.append(rec)
    df = pd.DataFrame(rows)[LQ_COLS]
    for lab, g in df.groupby("camp"):
        say(f"LQAS {lab}: {len(g)} lots, pass {(g.result=='Pass').sum()}, fail {(g.result=='Fail').sum()}")
    write_csv(df, out, "lqas.csv")
    return True


def build_lqas(raw, uc, out):
    if build_lqas_workbook(raw, uc, out):
        return
    rows = []
    for lab, p in files(raw, "LQAS_*.xls", r"^LQAS[_ ]").items():
        say("LQAS", lab)
        x = read_html_table(p)
        data = x[x.iloc[:, 6].map(lambda v: clean_code(v) is not None)]
        mon = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", lab, re.I)
        yr = re.search(r"(20\d\d)", lab)
        for _, r in data.iterrows():
            c = clean_code(r[6])
            if c not in uc.codes:
                continue
            d, t, u = uc.info(c)
            res = str(r[9])
            res = "Pass" if res.lower().startswith("pass") else "Fail" if res.lower().startswith("fail") else "Intermediate" if res.lower().startswith("inter") else ""
            typ = "CBV" if re.match(r"^(cbv|ccpv)", str(r[7]), re.I) else "NON-CBV"
            v = lambda i: (0 if pd.isna(num(pd.Series([r[i]])).iloc[0]) else num(pd.Series([r[i]])).iloc[0])
            rec = dict.fromkeys(LQ_COLS, "")
            rec.update({"camp": camp_label(r[0]) or lab, "month": mon.group(1).upper() if mon else "", "year": yr.group(1) if yr else "",
                        "district": d, "tehsil": t, "uc": u, "code": c, "type": typ, "result": res, "lots": int(v(8)) or 1,
                        "away": v(10), "newborn": v(12), "migrant": v(14), "refused": v(16), "tnv": v(18), "tmc": v(20), "unknown": v(22),
                        "missed": v(24) or (v(10) + v(12) + v(14) + v(16) + v(18) + v(20) + v(22))})
            rows.append(rec)
        say(f"   {len(data)} lots rows; Pass {sum(1 for r in rows if r['result']=='Pass')}, Fail {sum(1 for r in rows if r['result']=='Fail')}, "
            f"Intermediate {sum(1 for r in rows if r['result']=='Intermediate')}")
    if rows:
        write_csv(pd.DataFrame(rows)[LQ_COLS], out, "lqas.csv")


# ------------------------------------------------------------------ PCHS / PCM (post-campaign household survey / monitoring)
PC_COLS = ["camp", "district", "tehsil", "uc", "code", "checked", "fm", "missed", "nt", "tvbmc", "na", "refusal", "zero", "fake"]
PCM_COLS = ["camp", "district", "tehsil", "uc", "code", "checked", "fm", "notfm", "r_novisit", "r_missed", "r_away", "r_refused"]


PCHS_SKIP_ROUNDS = {"SNID Sep 2025"}      # rounds left out of the PCHS page on request


def pc_camp(s):
    """round label as used elsewhere in the dashboard: 'NID Apr 26' -> 'NID Apr 2026', 'SNID Sept 2026' -> 'SNID Sep 2026'"""
    s = camp_label(s)
    s = re.sub(r"^KP[- ]Special[- ]*\d*[- ]*", "Special ", s, flags=re.I)     # 'KP-Special-1-Jul 2026' / 'KP Special Aug 2026' -> 'Special Jul 2026' (as in LQAS)
    return re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d\d)$", r"\1 20\2", s, flags=re.I)


def _wb(raw):
    for n in ("UC Wise Data Sets.xlsx", "UC_Wise_Data_Sets.xlsx"):
        if os.path.exists(os.path.join(raw, n)):
            return os.path.join(raw, n)


def _code_for(uc, district_raw, tehsil, name, code=None):
    c = clean_code(code)
    if c in uc.codes:
        return c
    d = DISTRICTS.get(str(district_raw).strip().upper())
    return uc.code_of_name(d, name) if d else None


def build_pchs(raw, uc, out):
    """PCHS: sheet 'PCHS' of the UC-wise workbook (all rounds, matched by UC name) + any 'PCHS*Data*.xlsx' sheets that carry a UC code
    (a sheet counts as a round when its 'Campaign' column holds a single real label; copy/drag-corrupted labels are ignored)."""
    recs = {}

    def add(camp, district, tehsil, name, code, v):
        c = _code_for(uc, district, tehsil, name, code)
        if not c:
            return False
        d, t, u = uc.info(c)
        recs[(camp, c)] = dict(zip(PC_COLS, [camp, d, t, u, c] + v))
        return True

    wb = _wb(raw)
    if wb and "PCHS" in pd.ExcelFile(wb).sheet_names:
        x = pd.read_excel(wb, "PCHS")
        miss = 0
        for _, r in x.iterrows():
            if pd.isna(r.get("ActivityName")):
                continue
            n = lambda k: 0 if pd.isna(r.get(k)) else int(r.get(k))
            if not add(pc_camp(r["ActivityName"]), r["District"], r["Tehsil"], r["UC Name"], None,
                       [n("Total Children in HH"), n("Total Children FM"), n("Total Missedd"), n("NT"), n("TVBMC"), n("NA"), n("Refusal"), n("Zero Dose"), n("FFM")]):
                miss += 1
        if miss:
            warn(f"PCHS (workbook): {miss} rows not in the 120-UC list were skipped")
    for p in sorted(glob.glob(os.path.join(raw, "**", "PCHS*.xlsx"), recursive=True)):
        xl = pd.ExcelFile(p)
        for sh in xl.sheet_names:
            d = xl.parse(sh, header=None)
            hr = next((i for i in range(min(4, len(d))) if "District" in d.iloc[i].astype(str).tolist()), None)
            if hr is None or "UC Code" not in d.iloc[hr].astype(str).tolist():
                continue
            d.columns = [str(c).strip() for c in d.iloc[hr]]
            d = d.iloc[hr + 1:]
            ck = next((c for c in d.columns if c.startswith("Campaign")), None)
            if ck is None or d[ck].astype(str).nunique() != 1:
                continue                                    # no campaign column or a drag-corrupted one (NID May 26, 27, 28 ...)
            camp = pc_camp(d[ck].iloc[0])
            cols = list(d.columns)
            g = lambda r, *names: next((r[c] for c in names if c in cols and not pd.isna(r[c])), 0)
            k = 0
            for _, r in d.iterrows():
                if (camp, clean_code(r["UC Code"])) in recs:
                    continue
                v = [g(r, "Total Children Checked"), g(r, "Total Children Found FM"), g(r, "Total Missedd"), g(r, "NT"), g(r, "TVBMC"),
                     next((r[c] for c in cols if c in ("nan", "NA", "Not Available") and not pd.isna(r[c])), 0), g(r, "Refusal"), g(r, "Zero Dose"), g(r, "Fake FM")]
                v = [int(num(pd.Series([a])).fillna(0).iloc[0]) for a in v]
                k += add(camp, r["District"], r["Tehsil"], r["UC"], r["UC Code"], v)
            say(f"PCHS {camp}: {k} extra UCs from {os.path.basename(p)} [{sh}]")
    if not recs:
        return
    df = pd.DataFrame(list(recs.values()))[PC_COLS]
    df = df[(df.checked > 0) & ~df.camp.isin(PCHS_SKIP_ROUNDS)]
    for lab, g in df.groupby("camp"):
        say(f"PCHS {lab}: {len(g)} UCs ({', '.join(sorted(g.district.unique()))}), FM {g.fm.sum()/g.checked.sum():.1%}")
    write_csv(df, out, "pchs.csv")


def build_pcm(raw, uc, out):
    """PCM status table (Camp Name, Province, Division, District, Tehsil, UC, checked, FM, not FM, %, 4 reasons) - matched by UC name"""
    rows = []
    srcs = [p for p in glob.glob(os.path.join(raw, "**", "PCM*.xlsx"), recursive=True)]
    wb = _wb(raw)
    if wb and "PCM Status" in pd.ExcelFile(wb).sheet_names and not srcs:
        srcs = [wb]
    for p in sorted(srcs):
        xl = pd.ExcelFile(p)
        sh = "PCM Status" if "PCM Status" in xl.sheet_names else xl.sheet_names[0]
        d = xl.parse(sh, header=None)
        hr = next((i for i in range(min(8, len(d))) if "Camp Name" in d.iloc[i].astype(str).tolist()), None)
        if hr is None:
            continue
        d.columns = [str(c).strip() for c in d.iloc[hr]]
        d = d.iloc[hr + 1:]
        miss = 0
        for _, r in d.iterrows():
            if pd.isna(r.get("Province")) or pd.isna(r.get("UC")):
                continue                                # totals, notes
            c = _code_for(uc, r["District"], r["Tehsil"], r["UC"])
            if not c:
                miss += 1; continue
            dd, t, u = uc.info(c)
            n = lambda k: 0 if pd.isna(r.get(k)) else int(r.get(k))
            ch = n("Total Children Checked (0-59)")
            rows.append(dict(camp=pc_camp(r["Camp Name"]), district=dd, tehsil=t, uc=u, code=c, checked=ch, fm=n("Total Children FM (0-59)"),
                             notfm=n("Total Children Not FM (0-59)"), r_novisit=n("Team Did Not Visit"), r_missed=n("Team Missed Child"),
                             r_away=n("Child Away"), r_refused=n("Refused")))
        if miss:
            warn(f"PCM: {miss} UC names could not be matched to the master list")
    if not rows:
        return
    df = pd.DataFrame(rows)[PCM_COLS].drop_duplicates(["camp", "code"], keep="last")
    for lab, g in df.groupby("camp"):
        say(f"PCM {lab}: {len(g)} UCs, FM {g.fm.sum()/g.checked.sum():.1%}")
    write_csv(df, out, "pcm.csv")


MS_COLS = ["camp", "district", "tehsil", "uc", "code", "site", "loc", "date", "monitor", "desg", "checked", "fm", "missed",
           "r_nt", "r_tm", "r_away", "r_ref", "r_oth"]
MS_DESG = {"UCPO": "UC Polio Officer", "DPO": "District Polio Officer", "TPO_TSA": "TPO / TSA", "TEHSIL_POLIO_OFF": "Tehsil Polio Officer",
           "N_STOP": "N-STOP Officer", "PEO": "PEO", "UCSP": "UC Support Person", "OTHER": "Other", "DHO": "DHO", "EPI_COORDINATOR": "EPI Coordinator"}
MS_LOC = {"hospital": "Hospital", "market": "Market", "bus_stop": "Bus stop", "public_place": "Public place"}


def build_ms(raw, uc, tmp, out):
    """Market survey (children checked for a finger-mark at markets, bus stops, hospitals): one row per site visit, Bannu Division UCs only (archived rows included).
    missed = checked - finger-marked; the reasons are the five reason columns of the form."""
    rows = []
    for lab, p in files(raw, "Market_Survey*.zip", r"^Market[_ ]Survey[_ ]*").items():
        say("Market survey", lab)
        d = zip_csv(p, tmp)
        d["code"] = d["fk_uc_id"].map(clean_code)
        d = d[d.code.isin(uc.codes)]     # archived rows are kept: they are real site visits and the division totals include them
        for c in ["child_0_59_checked", "child_0_59_marked", "child_0_11_checked", "child_0_11_marked", "child_12_59_checked", "child_12_59_marked",
                  "team_not_visited", "team_missed_child", "child_away", "refusal", "other", "total_missed"]:
            d[c] = num(d[c]).fillna(0) if c in d else 0.0
        camp = pc_camp(lab)
        bad = 0
        for _, r in d.iterrows():
            ch = int(r.child_0_59_checked + r.child_0_11_checked + r.child_12_59_checked)
            fm = int(r.child_0_59_marked + r.child_0_11_marked + r.child_12_59_marked)
            rs = [int(r.team_not_visited), int(r.team_missed_child), int(r.child_away), int(r.refusal), int(r.other)]
            if ch - fm != sum(rs):
                bad += 1
                rs[4] += (ch - fm) - sum(rs)            # anything the form did not explain goes to 'Other'
                rs[4] = max(rs[4], 0)
            dd, t, u = uc.info(r.code)
            dt = pd.to_datetime(r.get("date_conducted"), errors="coerce")
            desg = str(r.get("designation", "")).strip()
            rows.append(dict(camp=camp, district=dd, tehsil=t, uc=u, code=r.code,
                             site=re.sub(r"\s+", " ", str(r.get("survey_loc", "")).strip()),
                             loc=MS_LOC.get(str(r.get("location_type", "")).strip().lower(), str(r.get("location_type", "")).strip()),
                             date="" if pd.isna(dt) else dt.strftime("%Y-%m-%d"),
                             monitor=re.sub(r"\s+", " ", str(r.get("surveyor_name", "")).strip()),
                             desg=MS_DESG.get(desg.upper(), desg), checked=ch, fm=fm, missed=max(ch - fm, 0),
                             r_nt=rs[0], r_tm=rs[1], r_away=rs[2], r_ref=rs[3], r_oth=rs[4]))
        if bad:
            warn(f"Market survey {lab}: {bad} site rows where children missed did not equal the sum of the reasons (difference put under 'Other')")
    if not rows:
        return
    df = pd.DataFrame(rows)[MS_COLS]
    df = df[df.checked > 0]
    for lab, g in df.groupby("camp"):
        say(f"Market survey {lab}: {len(g)} sites, {g.checked.sum():,} children, FM {g.fm.sum()/g.checked.sum():.1%}")
    write_csv(df, out, "ms.csv")


# ------------------------------------------------------------------ geo
def build_geo(raw, uc, out):
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    ucg = [p for p in glob.glob(os.path.join(raw, "*.geojson")) if "uc" in os.path.basename(p).lower()]
    dg = [p for p in glob.glob(os.path.join(raw, "*.geojson")) if "district" in os.path.basename(p).lower()]
    if not ucg:
        warn("no UC-wise geojson found - geo.json not built"); return
    say("Geo")
    feats = json.load(open(ucg[0]))["features"]
    by_code = {}
    for f in feats:
        by_code.setdefault(clean_code(f["properties"].get("UC Code")), []).append(f)

    def rings(geom, tol):
        g = geom.simplify(tol, preserve_topology=True)
        polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        return [[[round(x, 5), round(y, 5)] for x, y in p.exterior.coords] for p in polys if not p.is_empty]

    out_list, uc_geoms = [], {}
    for code in sorted(uc.codes):
        cand = by_code.get(code, [])
        d, t, u = uc.info(code)
        if not cand:      # code missing in the shapefile: match by district + UC name
            cand = [f for f in feats if norm(f["properties"].get("UC")) == norm(u) and
                    DISTRICTS.get(str(f["properties"].get("District")).upper()) == d]
        if len(cand) > 1:
            m = [f for f in cand if norm(f["properties"].get("UC")) == norm(u)]
            cand = m or cand
        if not cand:
            warn(f"geo: no boundary for UC {u} ({code})"); continue
        g = shape(cand[0]["geometry"]).buffer(0)
        uc_geoms[code] = g
    # districts
    dist_geoms = {}
    if dg:
        for f in json.load(open(dg[0]))["features"]:
            n = DISTRICTS.get(str(f["properties"].get("WHO_DIST_N")).upper())
            if n:
                dist_geoms[n] = shape(f["geometry"]).buffer(0)
    for n in DISTRICTS.values():
        if n not in dist_geoms:
            dist_geoms[n] = unary_union([g for c, g in uc_geoms.items() if uc.info(c)[0] == n])
    for n, g in dist_geoms.items():
        out_list.append({"l": "DIST", "d": n, "t": "", "n": n, "r": rings(g, 0.0008)})
    teh = {}
    for c, g in uc_geoms.items():
        d, t, u = uc.info(c)
        teh.setdefault((d, t), []).append(g)
    for (d, t), gs in sorted(teh.items()):
        out_list.append({"l": "TEH", "d": d, "t": t, "n": t, "r": rings(unary_union(gs), 0.0004)})
    for c, g in uc_geoms.items():
        d, t, u = uc.info(c)
        out_list.append({"l": "UC", "d": d, "t": t, "n": u, "r": rings(g, 0.0003)})
    json.dump(out_list, open(os.path.join(out, "geo.json"), "w"), separators=(",", ":"))
    open(os.path.join(out, "geo.updated.txt"), "w").write(NOW)
    say(f"  wrote geo.json: {sum(1 for x in out_list if x['l']=='UC')} UCs, {sum(1 for x in out_list if x['l']=='TEH')} tehsils, "
        f"{sum(1 for x in out_list if x['l']=='DIST')} districts, {os.path.getsize(os.path.join(out,'geo.json'))//1024} KB")


# ------------------------------------------------------------------ main
def main(raw, out):
    raw = stage(raw)
    os.makedirs(out, exist_ok=True)
    ul = load_uc_list(raw)
    if ul is None:
        sys.exit("Tehsil_File.xlsx (District, Tehsil, UC, Ucode) is needed in the RAW folder")
    uc = UCs(ul)
    say(f"{len(uc.codes)} UCs in the master list")
    with tempfile.TemporaryDirectory() as tmp:
        for fn, args in [(build_icm, (raw, uc, tmp, out)), (build_team, (raw, uc, tmp, out)), (build_aicmon, (raw, uc, tmp, out)), (build_zerolock, (raw, uc, tmp, out)),
                         (build_neap, (raw, uc, out)), (build_mpqa, (raw, uc, out)), (build_aictrain, (raw, uc, out)),
                         (build_teamtrain, (raw, uc, out)), (build_lqas, (raw, uc, out)),
                         (build_pchs, (raw, uc, out)), (build_pcm, (raw, uc, out)), (build_ms, (raw, uc, tmp, out)), (build_geo, (raw, uc, out))]:
            try:
                fn(*args)
            except Exception as e:      # one broken file must not stop the others
                import traceback; traceback.print_exc()
                warn(f"{fn.__name__} failed: {e}")
    if REPORT:
        say("\nThings to check:")
        for r in REPORT:
            say(" -", r)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
