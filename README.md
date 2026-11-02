# 🎨 Remote Design Job Alert Bot → Telegram

Checks thousands of company careers pages plus remote job boards about every 30 minutes. When a **new remote, US-eligible graphic/visual design role** appears, it sends you a Telegram message.

- **Where it looks:** company job pages hosted on Greenhouse, Lever, Ashby, Workable and SmartRecruiters (the systems behind most "Careers" pages). It also checks Remotive, Remote OK, We Work Remotely and Himalayas, plus any Workday careers sites you add.
- **How it finds companies:** every Sunday it searches the public Common Crawl web index for new company job pages. You don't need a list.
- **Cost:** $0. It runs on GitHub's free servers, so your computer can be off.
- **Setup time:** about 15 minutes, no coding.

---

## Setup

### 1. Create your Telegram bot (3 min)
1. In Telegram, search for **@BotFather** (blue checkmark) and press **Start**.
2. Send `/newbot`. Pick any name, then a username ending in `bot` (e.g. `olena_jobs_bot`).
3. BotFather replies with a **token** like `7512345678:AAH...`. Copy it and keep it private.
4. Tap the link to your new bot and press **Start**. The bot uses this to find your chat.

### 2. Put the bot on GitHub (7 min)
1. Create a free account at **github.com** if you don't have one.
2. Click **+ → New repository**. Name it `job-alert-bot`, choose **Public**, and click **Create repository**.
   *Why public: public repos get unlimited free run time. Nobody sees your Telegram token, because it's stored as a hidden secret. Want it private instead? See the FAQ below.*
3. On the new repo page, click **uploading an existing file**.
4. Unzip `job-alert-bot.zip` and drag **everything inside the folder** onto the page, including the hidden **`.github`** folder.
   - Mac: in Finder press **Cmd + Shift + .** to show hidden folders.
   - Windows: in File Explorer, open **View → Show → Hidden items**.
5. Click **Commit changes**.
6. Check that the repo now has a `.github/workflows` folder. If it doesn't, see "The .github folder didn't upload" below.

### 3. Add your Telegram token as a secret (2 min)
1. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `TELEGRAM_BOT_TOKEN`. Secret: paste the token from BotFather. Click **Add secret**.

### 4. Start it (1 min)
1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Check for new jobs → Run workflow**. Within about 2 minutes, Telegram should show:
   - ✅ *Connected!*
   - 👋 *Job alert bot is live*, plus the design roles open right now at the ~85 starter companies.
3. Click **Discover companies → Run workflow**. This first search takes 1–4 hours and adds thousands of companies. When it finishes, you'll get a Telegram summary, and the job check runs automatically.

After that, the job check runs every ~30 minutes and company discovery runs every Sunday.

---

## What the alerts look like

**One new job:**
```
🎨 Senior Graphic Designer
🏢 Acme
📍 Remote - US
🕒 12 min ago · via Greenhouse
Open & apply →
```
**Several at once:** one message that lists them all.

**❔ / "country not stated":** the job says "Remote" without naming a country. It's sent anyway, so check the listing.

---

## Change what you get
Edit **`config.yaml`** on GitHub (open the file → pencil icon → **Commit changes**):

| Want to… | Change |
|---|---|
| Add a job title | add a line under `title_include`, e.g. `- "creative producer"` |
| Block a job title | add a line under `title_exclude` |
| Also get Product/UX/UI designer roles | `include_product_ux: true` |
| Skip jobs that don't name a country | `allow_unclear_remote: false` |
| Watch a company on Workday (hospitals, big corporations) | paste its careers URL under `workday_sites` |
| Always watch a specific company | add its ID to `data/seed_companies.json`. The ID is the part after the domain: `boards.greenhouse.io/`**`figma`**, `jobs.lever.co/`**`spotify`**, `jobs.ashbyhq.com/`**`notion`** |

---

## How it works (short version)
- `bot/monitor.py` runs every 30 min. It checks companies that have posted design roles before, plus your seed list, **every run**. All other companies are checked in rotation, each at least every ~2 hours. This keeps the bot fast and polite to the sites it checks.
- It remembers which jobs it already sent (`data/state.json`), so you never get the same job twice. A job listed on both a company page and a job board is also sent only once.
- When a new company is discovered, only jobs it posted in the **last 48 hours** are sent, so its old postings don't flood you.
- `bot/discover.py` runs weekly and adds new companies to `data/companies.json`. Company IDs that stop working are skipped automatically.

## FAQ
**Alerts sometimes come 40–60 minutes apart, not 30.** GitHub can start scheduled runs late when its servers are busy. That's normal.

**I want the repo private.** Private repos get 2,000 free minutes per month, and running every 30 minutes uses more than that. In `.github/workflows/monitor.yml`, change `"*/30 * * * *"` to `"0 * * * *"` (hourly).

**Nothing arrives.** Open **Actions**, click the latest run, and read the red ❌ step. The two common causes:
- The token is missing or wrong (step 3).
- You never pressed **Start** in your bot. Press it, then run the workflow again.

**The .github folder didn't upload.** In the repo, click **Add file → Create new file** and type the name `.github/workflows/monitor.yml`. Paste in the contents of that file from the zip and click **Commit**. Repeat for `.github/workflows/discover.yml`.

**I got an email that scheduled workflows were disabled.** GitHub pauses them after 60 days with no repo activity. Click the link in the email to re-enable. The bot saves its memory file regularly, which usually prevents this.

**Pause the bot:** Actions → *Check for new jobs* → **⋯ → Disable workflow**.
