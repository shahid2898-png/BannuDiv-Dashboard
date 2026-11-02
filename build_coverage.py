#!/usr/bin/env python3
"""
build_coverage.py  -  raw campaign-coverage exports  ->  data/coverage.csv  (Bannu Division dashboard)

HOW TO ADD A NEW ROUND
  1. Drop the new raw files in the RAW folder (names end with the round, e.g. ..._SNID_Nov_2026.csv):
        Export_tbl_controlroom_<ROUND>.csv      Export_tbl_catchup_<ROUND>.csv
        Export_tbl_aftercatchup_<ROUND>.csv     Export_tbl_leftover_23_area_<ROUND>.csv
        NEAP_<ROUND>.csv
  2. python build_coverage.py RAW_FOLDER OUT_FOLDER
  Every round found in the folder is rebuilt, so old rounds stay and the dashboard picks up the new one.

Optional  uc_master.csv  in RAW folder (columns: ucode,risk,camp,campstatus,comnet) fills the filter fields.
"""
import csv, sys, glob, os, re
import pandas as pd

# raw district code  ->  name used in the dashboard / map file  (edit if your boundary file spells them differently)
DISTRICTS = {"BANNU": "BANNU", "LAKKIMRWT": "LAKKI MARWAT", "WAZIR-N": "WAZIR-N"}
LEFTOVER_FIRST_INTERVAL = 1          # report_interval 1 -> day code LO1 (interval 0 only occurs on blank placeholder rows)
INCLUDE_UNREPORTED_UCS = True        # UCs that are in NEAP but sent no campaign-day data yet: keep them in the target (empty row)
_NEAP_ROWS = None


def fix_month(s):
    """'NID APR 2026' -> 'NID Apr 2026' (the month is written in capitals in some exports)"""
    s = re.sub(r"\bSept(?:ember)?\b(?=\s*\d{4})", "Sep", str(s), flags=re.I)
    s = re.sub(r"\b(January|February|March|April|June|July|August|October|November|December)\b(?=\s*\d{4})", lambda m: m.group(1)[:3], s, flags=re.I)
    return re.sub(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b(?=\s*\d{4})", lambda m: m.group(1).title(), s)

def read_raw(path):
    """Tolerant reader: repeated headers renamed, short/blank/trailing rows dropped, long rows trimmed."""
    rows = list(csv.reader(open(path, encoding="utf-8-sig", errors="replace")))
    head, seen, names = rows[0], {}, []
    for c in head:
        seen[c] = seen.get(c, 0) + 1
        names.append(c.strip() if seen[c] == 1 else f"{c.strip()}__{seen[c]}")
    n, data, fixed = len(names), [], []
    for i, r in enumerate(rows[1:], 2):
        if not any(x.strip() for x in r) or len(r) < n - 1:
            continue                                   # blank / junk trailer
        if len(r) > n:
            fixed.append(i)                            # extra comma somewhere in the row
        data.append((r + [""] * (n - len(r)))[:n])
    df = pd.DataFrame(data, columns=names)
    for c in df.columns:
        df[c] = df[c].str.strip()
    if fixed:
        print(f"  ! {os.path.basename(path)}: row(s) {fixed} have extra fields (columns may be shifted) - check the source")
    return df

def num(s):
    return pd.to_numeric(s, errors="coerce")

def pick(df, lower_names):
    """case-insensitive column lookup"""
    low = {c.lower(): c for c in df.columns}
    return [low[n.lower()] for n in lower_names if n.lower() in low]

def keep_districts(df, col):
    df[col] = df[col].str.upper()
    return df[df[col].isin(DISTRICTS)].copy()

# ---- column maps: raw name -> name used by the dashboard --------------------------------------------------
CD = ["HH059", "CovNA059", "CovRef059", "OutofH059", "Transit059", "FixSite059", "Guests", "VaccinatedSchool",
      "RecNA059", "RecRef059", "HH059_MP", "CovMob", "reg_mob", "NewBorn", "ZeroRt", "zero_houses", "HHvisit",
      "HHPlan", "Weakness", "locked_houses", "OPVGiven", "OPVUsed"]
CU = {"CCovNA059": "CCovNA059", "CCovRef059": "CCovRef059", "UnRecorded_Cov": "UnRecorded_Cov",
      "COutofH059": "COutofH059", "CTransit059": "CTransit059", "CFixSite059": "CFixSite059", "CGuests": "CGuests",
      "CCovMob": "CCovMob", "CNewBorn": "CNewBorn", "ZeroDos1": "ZeroDos1", "c_zero_houses": "c_zero_houses",
      "AFP_CaseRpt": "AFP_CaseRpt", "c_locked_houses": "c_locked_houses", "OPVGiven": "OPVGiven", "OPVUsed": "OPVUsed"}
AC = {"ACCovNA059": "ACCovNA059", "ACCovRef059": "ACCovRef059", "UnRecCov": "UnRecCov", "ACOutofH059": "ACOutofH059",
      "ac_transit_059": "ac_transit_059", "ac_fix_sites_059": "ac_fix_sites_059", "ACGuests": "ACGuests",
      "ACCovMob": "ACCovMob", "ACNewBorn": "ACNewBorn", "ac_zero_dose5": "ac_zero_dose5",
      "ac_zero_houses": "ac_zero_houses", "ac_afp_case_rpt": "ac_afp_case_rpt", "ac_locked_houses": "ac_locked_houses",
      "OPVGiven": "OPVGiven", "OPVUsed": "OPVUsed"}
LO_COLS = ["bycots_covered", "security_na_covered", "security_aval_noteam_covered", "security_other_covered",
           "snow_bound_covered", "other_covered", "postpone_covered", "bycots_recorded", "security_na_recorded",
           "security_aval_noteam_recorded", "security_other_recorded", "snow_bound_recorded", "other_recorded",
           "postpone_recorded", "cumulative_covered_na", "cumulative_covered_ref", "cumulative_recorded_na",
           "cumulative_recorded_ref", "street_covered", "guest_covered", "newborn_covered"]

def out_columns():
    base = ["campaign", "district", "tehsil", "uc", "day", "tier", "risk", "camp", "campstatus", "comnet"]
    return (base + CD + ["Neap Target"] + ["Catchup1." + v for v in CU.values()]
            + ["AfterCatchup." + v for v in AC.values()] + ["Leftover2." + c for c in LO_COLS])

def find_rounds(raw):
    """round label = text after 'controlroom_' in the file name"""
    return sorted({re.sub(r"\.csv$", "", os.path.basename(p).split("controlroom_", 1)[1])
                   for p in glob.glob(os.path.join(raw, "Export_tbl_controlroom_*.csv"))})

def build_round(raw, rnd, neap_hh=None, master=None, neap_ucid=None, neap_tier=None):
    f = lambda kind: os.path.join(raw, f"Export_tbl_{kind}_{rnd}.csv")
    parts, info = [], {}
    # ---- campaign days
    cr = read_raw(f("controlroom")); cr = keep_districts(cr, "District")
    cr["Cday"] = cr["Cday"].astype(str)
    cr = cr.sort_values("TimeStamp").drop_duplicates(["Ucode", "Cday"], keep="last")
    camp = fix_month(cr["ActivityName"].mode()[0])
    d = pd.DataFrame({"campaign": camp, "district": cr.District, "tehsil": cr.Tehsil.str.upper(), "uc": cr.UC.str.upper(),
                      "ucode": cr.Ucode, "ucid": cr.UCID, "day": "CD" + cr.Cday, "tier": num(cr.Tier)})
    for c in CD:
        d[c] = num(cr[c]) if c in cr else pd.NA
    parts.append(d)
    ref = d.drop_duplicates("ucode")[["ucode", "ucid", "district", "tehsil", "uc", "tier"]].set_index("ucode")
    # ---- catch-up / after catch-up (keyed by Ucode)
    for kind, pref, dayc, mapping, code in [("catchup", "Catchup1.", "Cday", CU, "CU"), ("aftercatchup", "AfterCatchup.", "cday", AC, "AC")]:
        p = f(kind)
        if not os.path.exists(p):
            continue
        t = keep_districts(read_raw(p), "District")
        t = t.sort_values("TimeStamp").drop_duplicates(["Ucode", dayc], keep="last")
        x = pd.DataFrame({"campaign": camp, "district": t.District, "tehsil": t.Tehsil.str.upper(), "uc": t.UC.str.upper(),
                          "ucode": t.Ucode, "ucid": t.UCID, "day": code + t[dayc].astype(str), "tier": num(t.Tier)})
        for raw_c, new_c in mapping.items():
            x[pref + new_c] = num(t[raw_c]) if raw_c in t else pd.NA
        parts.append(x)
    # ---- leftover (UC identified by UC id = UCID; blank 'NULL' placeholder rows dropped)
    p = f("leftover_23_area")
    if os.path.exists(p):
        t = read_raw(p); t["district"] = t["district"].str.upper(); t = t[t.district.isin(DISTRICTS)]
        null_rows = (t["UC_name"].str.upper() == "NULL") | (t["uc"] == "0")
        info["leftover_blank_rows_dropped"] = int(null_rows.sum())
        t = t[~null_rows]
        ucid2code = ref.reset_index().drop_duplicates("ucid").set_index("ucid")["ucode"]
        if neap_ucid is not None:                       # UCs that only reported leftover: find their UC code through NEAP
            ucid2code = pd.concat([ucid2code, neap_ucid[~neap_ucid.index.isin(ucid2code.index)]])
        t["ucode"] = t["uc"].map(ucid2code)
        info["leftover_unmatched"] = t[t.ucode.isna()][["district", "UC_name", "uc"]].values.tolist()   # kept, with names from the leftover row
        t["ucode"] = t["ucode"].fillna("UCID" + t["uc"])
        t["iv"] = num(t["report_interval"]).fillna(0).astype(int) - LEFTOVER_FIRST_INTERVAL + 1
        info["leftover_intervals_present"] = sorted(t.iv.unique().tolist())
        info["leftover_duplicate_submissions_kept"] = int(t.duplicated(["ucode", "iv"]).sum())   # kept: dashboard sums them
        t = t.sort_values("TimeStamp")
        x = pd.DataFrame({"campaign": camp, "ucode": t.ucode, "day": "LO" + t.iv.astype(str)})
        x = x.join(ref[["district", "tehsil", "uc", "tier", "ucid"]], on="ucode")
        miss = x.district.isna()
        if miss.any():
            x.loc[miss, "district"] = t.loc[x.index[miss], "district"].values
            x.loc[miss, "tehsil"] = t.loc[x.index[miss], "Tehsil"].str.upper().values
            x.loc[miss, "uc"] = t.loc[x.index[miss], "UC_name"].str.upper().values
        for c in LO_COLS:
            x["Leftover2." + c] = num(t[c]) if c in t else pd.NA
        parts.append(x)
    df = pd.concat(parts, ignore_index=True)
    # ---- UCs that are in NEAP but sent no data at all yet (round in progress): add one empty placeholder row
    #      so the NEAP target covers every UC (the target must equal the NEAP readiness figure).
    if INCLUDE_UNREPORTED_UCS and neap_hh is not None:
        miss_codes = [u for u in neap_hh.index if u not in set(df.ucode)]
        if miss_codes:
            nr = _NEAP_ROWS.loc[miss_codes]
            ph = pd.DataFrame({"campaign": camp, "district": nr["District"].str.upper().values, "tehsil": nr["Tehsil"].str.upper().values,
                               "uc": nr["UC"].str.upper().values, "ucode": miss_codes, "ucid": nr["UCID"].values if "UCID" in nr else "",
                               "day": "CD1", "tier": num(nr["Tier"]).values})
            df = pd.concat([df, ph], ignore_index=True)
            info["placeholder_rows_for_UCs_with_no_data_yet"] = [(r.uc, int(neap_hh[r.ucode])) for r in ph.itertuples()]
    # ---- NEAP target on first campaign-day row of each UC
    if neap_hh is not None:
        # target sits on the UC's first campaign-day row; a UC that only reported leftover areas carries it on its first LO row
        df["_o"] = df.day.str[:2].map({"CD": 0, "LO": 1, "CU": 2, "AC": 3}); df["_n"] = df.day.str[2:].astype(int)
        first = df.sort_values(["_o", "_n"]).drop_duplicates("ucode")
        first = first[first._o <= 1]
        df["Neap Target"] = pd.NA
        idx = first.index
        df.loc[idx, "Neap Target"] = df.loc[idx, "ucode"].map(neap_hh)
        df = df.drop(columns=["_o", "_n"])
        info["rows_with_no_neap_target"] = sorted(set(df.loc[idx][df.loc[idx, "Neap Target"].isna()].uc))
        info["uc_with_target_but_no_campaign_data"] = sorted({(df.loc[i, "uc"], int(df.loc[i, "Neap Target"])) for i in idx
                                                            if df.loc[i, "day"].startswith("LO") and pd.notna(df.loc[i, "Neap Target"])})
        info["neap_ucs_with_no_data_at_all"] = [(u, int(h)) for u, h in neap_hh.items() if u not in set(df.ucode)]
    if neap_tier is not None:
        df["tier"] = num(df["tier"]).fillna(df["ucode"].map(neap_tier))
    num_cols = [c for c in df.columns if c not in ("campaign","district","tehsil","uc","ucode","ucid","day")]
    for c in num_cols:
        df[c] = num(df[c])
    # ---- optional UC master (risk / camp / comnet)
    for c in ["risk", "camp", "campstatus", "comnet"]:
        df[c] = ""
        if master is not None:
            df[c] = df["ucode"].map(master[c]).fillna("")
    return df, info, camp

def load_neap(raw, rnd):
    p = os.path.join(raw, f"NEAP_{rnd}.csv")
    if not os.path.exists(p):
        return None
    n = read_raw(p); n["District"] = n["District"].str.upper(); n = n[n.District.isin(DISTRICTS)]
    n = n.sort_values("TimeStamp").drop_duplicates("Ucode", keep="last")
    ucid = n.drop_duplicates("UCID").set_index("UCID")["Ucode"]
    global _NEAP_ROWS
    _NEAP_ROWS = n.set_index("Ucode")
    return num(n.set_index("Ucode")["HH059_MP"]), ucid, num(n.set_index("Ucode")["Tier"])   # NEAP target = UC micro-plan target (validated)

def load_uc_list(raw):
    """Master list of the division's UCs (District, Tehsil, UC, Ucode) - Tehsil_File.xlsx or uc_list.csv in the RAW folder."""
    for name in ("Tehsil_File.xlsx", "uc_list.csv", "Tehsil_File.csv"):
        p = os.path.join(raw, name)
        if os.path.exists(p):
            t = pd.read_excel(p, dtype=str) if name.endswith("xlsx") else pd.read_csv(p, dtype=str)
            t.columns = [c.strip().lower() for c in t.columns]
            for c in t.columns:
                t[c] = t[c].str.strip()
            t["district"] = t["district"].str.upper().map(lambda x: DISTRICTS.get(x, x))
            return t.drop_duplicates("ucode").set_index("ucode")
    return None

def stage(raw):
    """Look through RAW_FOLDER and its sub-folders; file names are matched with spaces = underscores
    (so the folder can be pointed straight at 'Bannu Div Dashboard')."""
    import tempfile
    d = tempfile.mkdtemp(prefix="raw_")
    for root, _, fs in os.walk(raw):
        for f in fs:
            dst = os.path.join(d, re.sub(r"\s+", "_", f.strip()))
            if not os.path.exists(dst):
                os.symlink(os.path.abspath(os.path.join(root, f)), dst)
    return d

def main(raw, out):
    raw = stage(raw)
    os.makedirs(out, exist_ok=True)
    mp = os.path.join(raw, "uc_master.csv")
    master = pd.read_csv(mp, dtype=str).drop_duplicates("ucode").set_index("ucode") if os.path.exists(mp) else None
    frames, report = [], {}
    for rnd in find_rounds(raw):
        print("Round", rnd)
        neap = load_neap(raw, rnd)
        if neap is None:
            print("  ! no NEAP file for this round - HH coverage % cannot be calculated")
        df, info, camp = build_round(raw, rnd, neap[0] if neap else None, master, neap[1] if neap else None, neap[2] if neap else None)
        df["district"] = df["district"].map(DISTRICTS)
        frames.append(df); report[camp] = info
    allr = pd.concat(frames, ignore_index=True)
    ul = load_uc_list(raw)
    if ul is not None:                                   # canonical names + keep only the division's UCs
        known = allr.ucode.isin(ul.index)
        if (~known).any():
            print("  ! UC codes not in the UC list - rows excluded:", sorted(allr[~known].groupby(["campaign", "district", "uc", "ucode"]).size().index.tolist()))
        allr = allr[known].copy()
        for c, lc in (("district", "district"), ("tehsil", "tehsil"), ("uc", "uc")):
            allr[c] = allr.ucode.map(ul[lc]).str.upper()
        per = allr.groupby("campaign").ucode.nunique()
        missing = {c: sorted(set(ul.index) - set(allr[allr.campaign == c].ucode)) for c in per.index}
        print(f"UC list: {len(ul)} UCs | UCs reporting per round: {per.to_dict()} | UCs with no rows: " + str({k: len(v) for k, v in missing.items()}))
    cols = out_columns()
    for c in cols:
        if c not in allr.columns:
            allr[c] = ""
    order = {"CD": 0, "CU": 1, "AC": 2, "LO": 3}
    allr["_o"] = allr.day.str[:2].map(order); allr["_n"] = allr.day.str[2:].astype(int)
    allr = allr.sort_values(["campaign", "district", "tehsil", "uc", "_o", "_n"])
    allr[cols].to_csv(os.path.join(out, "coverage.csv"), index=False, float_format="%g")
    with open(os.path.join(out, "coverage.updated.txt"), "w") as fh:
        fh.write(pd.Timestamp.now().strftime("%Y-%m-%dT%H:%M:%S"))
    for camp, info in report.items():
        print(f"\n{camp}: {info}")
    print("\nWrote", os.path.join(out, "coverage.csv"), len(allr), "rows")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "raw", sys.argv[2] if len(sys.argv) > 2 else "data")
