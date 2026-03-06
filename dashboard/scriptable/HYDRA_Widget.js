// HYDRA Widget for iOS Scriptable
// Shows HYDRA trading bot P&L on home screen
//
// Setup:
// 1. Install Scriptable from App Store
// 2. Create new script, paste this code
// 3. Set DASHBOARD_URL below to your VM's external IP
// 4. Add Scriptable widget to home screen, select this script

const DASHBOARD_URL = "http://YOUR_VM_IP:8080";
const API_KEY = ""; // Set if you configured DASHBOARD_API_KEY

async function fetchData() {
  const url = `${DASHBOARD_URL}/api/widget${API_KEY ? `?api_key=${API_KEY}` : ""}`;
  const req = new Request(url);
  req.timeoutInterval = 10;

  try {
    const data = await req.loadJSON();
    // Cache for offline
    Keychain.set("hydra-widget-cache", JSON.stringify(data));
    return data;
  } catch {
    // Offline fallback
    try {
      return JSON.parse(Keychain.get("hydra-widget-cache"));
    } catch {
      return null;
    }
  }
}

function createWidget(data) {
  const w = new ListWidget();
  w.backgroundColor = new Color("#2d353f");
  w.setPadding(12, 14, 12, 14);

  if (!data) {
    const t = w.addText("HYDRA Offline");
    t.font = Font.mediumSystemFont(14);
    t.textColor = new Color("#8b9bb0");
    return w;
  }

  // Title row
  const titleStack = w.addStack();
  titleStack.layoutHorizontally();
  titleStack.centerAlignContent();

  const title = titleStack.addText("HYDRA");
  title.font = Font.boldMonospacedSystemFont(13);
  title.textColor = new Color("#7ee8c7");

  titleStack.addSpacer();

  const statusDot = titleStack.addText(data.market_open ? "●" : "○");
  statusDot.font = Font.systemFont(8);
  statusDot.textColor = new Color(data.market_open ? "#7ee8c7" : "#5e6e82");

  w.addSpacer(6);

  // P&L (large)
  const pnl = data.net_pnl || 0;
  const sign = pnl >= 0 ? "+" : "";
  const pnlText = w.addText(`${sign}$${Math.abs(pnl).toFixed(0)}`);
  pnlText.font = Font.boldMonospacedSystemFont(28);
  pnlText.textColor = new Color(pnl >= 0 ? "#7ee8c7" : "#f85149");
  pnlText.minimumScaleFactor = 0.5;

  w.addSpacer(4);

  // Entry dots
  const dotStack = w.addStack();
  dotStack.layoutHorizontally();
  dotStack.spacing = 4;

  const dotColors = {
    active: "#58a6ff",
    expired: "#7ee8c7",
    stopped: "#f85149",
    pending: "#5e6e82",
  };

  (data.entry_dots || []).forEach((status) => {
    const dot = dotStack.addText("●");
    dot.font = Font.systemFont(10);
    dot.textColor = new Color(dotColors[status] || "#5e6e82");
  });

  w.addSpacer(4);

  // Stats row
  const statsStack = w.addStack();
  statsStack.layoutHorizontally();

  const entries = statsStack.addText(`${data.entries || 0} entries`);
  entries.font = Font.systemFont(10);
  entries.textColor = new Color("#8b9bb0");

  statsStack.addSpacer();

  const stops = statsStack.addText(`${data.stops || 0} stops`);
  stops.font = Font.systemFont(10);
  stops.textColor = new Color(
    (data.stops || 0) > 0 ? "#f85149" : "#8b9bb0"
  );

  // Tap action
  w.url = DASHBOARD_URL;

  // Refresh every 15 minutes
  const refreshDate = new Date();
  refreshDate.setMinutes(refreshDate.getMinutes() + 15);
  w.refreshAfterDate = refreshDate;

  return w;
}

const data = await fetchData();
const widget = createWidget(data);

if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  widget.presentSmall();
}

Script.complete();
