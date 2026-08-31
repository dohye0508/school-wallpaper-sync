# School Wallpaper Sync (Daily Timetable & Meal Scheduler)

A beautiful Windows desktop assistant that automatically syncs and displays your daily class timetable and school meals directly on high-quality aesthetic wallpapers.

---

## ✨ Features

- **Real-time Sync**: Fetches your today's timetable and meals instantly via the official [NEIS Open API](https://open.neis.go.kr).
- **Aesthetic Backgrounds**: Automatically rotates daily through 8 built-in premium wallpapers (or choose to pin your favorite one, or upload your own custom photo).
- **Minimalist Layout**: Clean typography overlaid directly on the wallpaper with no bulky cards or boxes, maintaining a sleek desktop design.
- **Silent Background Updates**: Updates automatically in the background using Windows Task Scheduler (no need to keep any app open).
- **Interactive Settings GUI**: Double-click to configure your school information, adjust text sizes, alignment, colors, and shadows with real-time live preview.

---

## 🚀 How to Get Started

### Step 1: Get a Free NEIS API Key
1. Go to the [NEIS Open API Portal](https://open.neis.go.kr) and sign up for a free account.
2. Request a key for the "School Info & Timetable/Meals" endpoint. Your key will be activated instantly.

### Step 2: Configure Your School Info
1. Open the settings tool (`학교바탕화면설정.exe`) in the main folder.
2. Paste your **NEIS Open API Key**.
3. Type your **School Name** and click **Search**. Select your school from the results list.
4. Enter your **Grade** and **Class** numbers.
5. Click **Save and Apply Now** to apply the wallpaper immediately!

### Step 3: Customize Colors & Layouts
- Go to the **Layout & Design Settings** tab.
- Click any element color button to pick custom hex colors.
- Adjust sliders (X/Y coordinates, Font Size, Shadow Blur/Opacity) to arrange elements perfectly.
- Slide and release; adjustments are synced to your desktop live in under 100 milliseconds!
- Want to go back? Simply click the **Reset** (초기화) button at the bottom to return to the clean centered default style.

---

## ⏰ Set Up Automatic Updates (Task Scheduler)
To have your timetable and meal info update automatically when periods change, register the background engine to run on Windows Task Scheduler:

1. Open **Task Scheduler** in Windows.
2. Click **Create Basic Task...** and name it (e.g., `School Wallpaper Sync`).
3. Set the trigger to **Daily** or **When I log on**.
4. In the Actions step, select **Start a Program** and browse to:
   `dist/SchoolWallpaper/SchoolWallpaper.exe`
5. After creating the task, open its properties, go to the **Triggers** tab, edit the trigger, and check **Repeat task every:** `5` or `10` minutes for a duration of **Indefinitely**.
6. (Recommended) In the **Conditions** tab, uncheck **Start the task only if the computer is on AC power** so it runs even on laptop battery.

---

## 🎨 License

Feel free to use and modify for personal purposes. Keep your study and meals organized beautifully!
