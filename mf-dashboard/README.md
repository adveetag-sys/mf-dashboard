# My Mutual Funds dashboard

A Streamlit dashboard for the funds in `holdings.xlsx`. GitHub Actions refreshes the data every day, so you never update it by hand and it uses no Claude usage.

## What updates itself

| What | Source | When |
|---|---|---|
| NAV and returns (1D to 5Y, monthly NAV change grid) for your exact plan | AMFI data via mfapi.in | every day, 07:10 IST |
| Each fund's stock-wise portfolio | Groww scheme data (Direct plan page: same portfolio as Regular) | checked every 3 days; a new month is saved when the AMC publishes (usually by the 10th–15th) |
| Factsheet numbers: AUM, expense ratio, P/E, manager, benchmark, exit load, returns vs category | Groww, with Kuvera as backup | same as above |
| Monthly changes: new buys, full exits, adds, trims, sector shifts | worked out from the saved monthly portfolios | as soon as two months are on record |
| Factsheet study: plain-English notes on concentration, cash, churn, expense ratio, AUM, manager change, P/E, performance vs category | rules in `mfdash/analysis.py` | every refresh |
| Across my funds: your exposure to each stock, sector mix, overlap between funds | worked out from the holdings | every refresh |

The first month-on-month comparison appears once the next disclosure is captured. Groww shows only the latest portfolio, so history starts from the first run.

## Your holdings file

`holdings.xlsx` needs only **ISIN** and **Mutual Fund Name** (your current file works as is). Optional columns, picked up automatically if you add them:

- **Units** and **Invested**: switch on your ₹ value, gain, XIRR and a value-by-month chart, and weight the "Across my funds" view by your money
- **Purchase Date**: needed for XIRR
- **AMFI Code** or **Groww Link**: only if the Data status tab shows a wrong match
- A second sheet named **Transactions** (Date, Fund, Amount, Units, Type) gives exact XIRR and month-end values when you invest by SIP

To change your funds, upload a new `holdings.xlsx` to the repo. The update runs on its own straight after.

## One-time setup (about 10 minutes)

1. **Make a private GitHub repo**, for example `mf-dashboard`. Keep it private because it holds your portfolio.
2. **Upload these files** with the folder structure kept, including the hidden `.github` and `.streamlit` folders. From a terminal:
   ```bash
   cd mf-dashboard
   git init -b main && git add . && git commit -m "MF dashboard"
   git remote add origin https://github.com/<your-username>/mf-dashboard.git
   git push -u origin main
   ```
3. **Let the workflow save data:** repo → Settings → Actions → General → Workflow permissions → *Read and write* → Save.
4. **First run:** Actions tab → *Update fund data* → *Run workflow*. It takes 2–4 minutes and commits `data/`.
5. **Deploy:** share.streamlit.io → *Create app* → pick the repo, branch `main`, file `app.py` → Deploy. Allow Streamlit to access private repos when asked. Under the app's *Share* settings, keep it to your own email.

## If something looks off

Open the **Data status** tab. It shows which scheme each of your rows was matched to, the Groww page used, and whether NAV and factsheet data loaded. Groww's data is unofficial; if it changes format, NAVs and returns keep working, and the log says which fields went missing.

## Run locally

```bash
pip install -r requirements.txt
python scripts/update.py      # fetch data
streamlit run app.py
python tests/test_pipeline.py # offline test with fake data
```
