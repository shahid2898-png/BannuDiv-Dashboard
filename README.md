# Bannu Division Polio Dashboard

Static site. Publish with GitHub Pages (Settings > Pages > Deploy from branch > main / root). ERM PPT and Post Campaign PPT download directly in any browser.

# Bannu Division Polio Dashboard

Static site (no server needed): `index.html` + `data/` + `assets/`. Host it on GitHub Pages (or any web folder) and open `index.html`.
Locally, serve the folder (`python3 -m http.server`) and open http://localhost:8000 — opening the file directly will not load the data.

## Adding a new round
1. Put the new raw exports in one folder (sub-folders are fine; spaces or underscores in names are both accepted), using the same names as before (round label = text after the prefix):
   `Export_tbl_controlroom_/catchup_/aftercatchup_/leftover_23_area_<ROUND>.csv`, `NEAP_<ROUND>.csv`,
   `ICM_<ROUND>.zip`, `ICM_Team_Monitoring_<ROUND>.zip`, `ICM_AIC_Monitoring_<ROUND>.zip`,
   `MPQA_<ROUND>.xls`, `AICs-UCMOs_Training_<ROUND>.xls`, `Teams_Training_<ROUND>.xls`, `LQAS_<ROUND>.xls`
   (e.g. `ROUND = SNID_Nov_2026`), plus `Tehsil_File.xlsx`, the two `.geojson` files and (optional) `UC Wise Data Sets.xlsx` — its eLQAS sheet supplies the LQAS file (all rounds) and its MPQA sheet supplies earlier MPQA rounds.
2. Run (needs pandas, openpyxl, lxml, shapely):
   `python build_coverage.py RAW_FOLDER data` then `python build_data.py RAW_FOLDER data`
3. Upload the changed `data/` files. Each page reads whatever rounds are present; a missing file just empties that page.

Only the 120 UCs in `Tehsil_File.xlsx` are kept. Monitor identities are replaced by codes (M-XXXXXX).

## Checked against your UC Wise Data Sets workbook
* Coverage: HH coverage 89.4 % (May) / 96.8 % (Sep), still missed 23,753 (Sep) - identical to the HH Coverage sheet.
* ICM: clusters, houses, recall checked/vaccinated, seen/finger-marked, NT, TVBMC, guests, fake FM, missed areas and poorly covered areas agree UC by UC. Poorly covered area = >2 missed children, TVBMC > 0 and no NT house.
* Team monitoring: all 24 items agree with the sheet (item % per UC); AIC monitoring items agree for SNID Sep 2026.
* Known differences: (1) Vitamin-A and "supported 5+ houses" exclude not-applicable forms (the dashboard's design); your sheet counts them as not done. (2) For SNID May 2026 your ICM sheet shows no-response / refusal houses blank; the raw data has values and they are shown.
* Boundary file: for most Bannu UCs the GPS points of the monitoring forms do not fall inside the polygon carrying the same UC code (only ~4 % do), also partly in Lakki Marwat / Wazir-N. The 2024 shapefile appears not to match the current UC list, so the GPS audit pages and UC map shapes are unreliable until a matching shapefile is supplied.

## Assumptions to confirm (mapped by comparing with the Peshawar files)
* ICM: the Sep form's single 0–59 m figure goes in the 12–59 m slot; archived records are dropped.
* Team / AIC monitoring: the 24 + 15 checklist positions follow the dashboard's item order; carrier cold-chain items come from the form's text field.
* NEAP: vaccine-carrier fields (`vca/vcr`) and zero-dose (`zd`) use `vacc_carr_avbl`, `vacc_carr_req`, `Dose0Cov`.
* Trainings: your reports are UC-level percentages, so a UC counts as one session with fractional answers.
* LQAS: taken from the eLQAS sheet (Pass/Fail), rounds NID Apr 2026 and SNID May 2026; no Sep LQAS yet.
* Earlier rounds (NID Apr 2026) exist only as UC summaries in the workbook, so only MPQA and LQAS show them.
* PCHS, PCM and the ICM catch-up cluster files are not part of the Peshawar layout and are not used yet.

## Contact details in the chatbot
`index.html` → search for `CREATOR=` and add the name, e-mail and phone you want the assistant to show. `ai.json` is empty (no AI back-end).


## Post Campaign Assessments (LQAS, PCHS, PCM, combined)
- `data/pchs.csv` is built from the PCHS sheet of `UC Wise Data Sets.xlsx` (all rounds, matched to the 120 UCs by name) plus any `PCHS*.xlsx` whose sheet has a single real Campaign label and a UC Code.
- `data/pcm.csv` is built from `PCM*.xlsx` (PCM status table, matched by UC name) or the workbook's PCM Status sheet.
- Add a new round: put the updated workbook / PCHS / PCM files in the raw folder and re-run `python build_data.py RAW data`.
- Pages: PCHS, PCM and "LQAS - PCHS - PCM" under Post Campaign Assessments. Standard used for finger-marked %: on target 95% or more, watch 90-95%, below standard under 90%.

## Training files
`AICs-UCMOs_Training_<ROUND>.zip` and `Teams_Training_<ROUND>.zip` are the raw monitoring-form exports (province-wide). The script keeps only the forms of the 120 UCs, drops archived forms and writes `aictrain.csv` / `teamtrain.csv`. The older UC-level `.xls` reports are still accepted for rounds that have no zip.

## NID Apr 2026
Added alongside SNID May/Sep/Nov 2026. Month names are normalised in `build_coverage.fix_month` ("NID APR 2026" -> "NID Apr 2026"); `build_icm` reads every `ICM_<round>.zip` whose name starts with NID/SNID/Special/KP. The NEAP readiness cards now read "# of UCs" and "UCMOs (UPEC)" (replacing UCMOs (UPEC) and Zonal supervisors).

## PowerPoint decks
- Post Campaign Review: review slides now include LQAS · PCHS · PCM trend, reasons missed, UCs below standard and a findings/actions slide; backup slides (after THANK YOU) add PCHS and PCM UC-wise tables and a three-tool UC comparison. Missing data is shown as "NC" / "not assessed", never zero.
- Evening Review Meeting: with "ICM analysis" ticked, the deck also gets Team monitoring and AIC monitoring slides (last 2 rounds, same day scope) and a Key findings & actions slide.

## PDF button
`window.print()` is blocked inside a published artifact, so the PDF button (and "Whole page as PDF" in the card menu) now builds the PDF in the browser: `assets/html2canvas.min.js` (html2canvas-pro) + `assets/jspdf.umd.min.js` + `assets/pagepdf.js`, saved through the downloads capability. It falls back to the print dialog if that fails.

## PDF button – page breaks
Pages are now cut only between cards/rows/charts (never through them); trailing blank page removed and short pages use the dark page background.

## MPQA pass / needs improvement / serious issues
The MPQA page now shows "rated Good" as a share of assessed UCs (same basis as the MPQA portal: Pass >=90, Needs improvement 70-<90, Serious issues <70, each as # and % of UCs assessed). The "overall score" tile remains the average of UC overall scores.

## Polio Assistant
Header has a Clear button (empties the chat, resets context, shows the welcome message again). Any reply that mentions "Zonal supervisor(s)" is shown as "UCMO(s) (UPEC)".

## ERM deck theme
The ERM PowerPoint now follows the Divisional Task Force deck style (Arial, dark-green title slide with gold label and orange rule, section label + green title + grey rules on content slides, green table headers with light-green highlight rows, WHO / Health Department header and footer with source and page number). Content and dialog controls are unchanged.

## Post Campaign PPT theme
The Post Campaign Review PowerPoint uses the same theme as the ERM deck (Arial, dark-green title / Thank you / Backup divider slides with gold label and orange rule, green titles with section label and grey rules, green table headers, light-green and peach panels, WHO / Health Department header, footer with source and page number). Content and dialog controls are unchanged.

Map slides in the Post Campaign PPT keep their original look (green banner, Calibri, original map colours, PEI logo footer); the rest of the deck uses the new theme.

## Sidebar
Left menu restyled: white rail, navy section pills, blue icons, light-blue active item with a blue bar, blue water-drop logo (same in dark and light mode).

## Professional style (optional)
Top-bar button "Professional" / "Classic" toggles `data-style=pro` (saved in localStorage `pdm-style`). Default stays Classic. To remove it permanently, delete the CSS block "Professional style" at the end of index.html and the `#tg-style` button/handler.
