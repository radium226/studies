// Make a desktop Firefox behave like a phone browser.
//
// Placeholders are substituted by session.sh from the container environment.

// --- the actual mobile-first part -------------------------------------------

// The framebuffer is sized in *physical* pixels, like a real phone panel, and
// this divides it down to a CSS viewport: 1080 / 2.4 = 450 CSS px.
//
// The ratio is not free to choose. Firefox refuses to make its window narrower
// than 450 CSS px, and --kiosk does not override that: pick a ratio that
// implies less and the window ends up *wider* than the screen, with the right
// edge of every page cut off. 1080x2400 at 2.4 lands exactly on the floor.
user_pref("layout.css.devPixelsPerPx", "@DEVICE_PIXEL_RATIO@");

// Sites gate mobile layout on input capability, not on width. 1 is Coarse
// here -- the bitfield is Coarse=1, Fine=2, Hover=4, which is easy to get
// backwards, and getting it backwards silently reports a mouse.
user_pref("ui.primaryPointerCapabilities", 1);
user_pref("ui.allPointerCapabilities", 1);

// Enabling touch events is not enough on its own: `ontouchstart in window`,
// which is what feature detection actually looks at, stays undefined until the
// legacy APIs are exposed too.
user_pref("dom.w3c_touch_events.enabled", 1);
user_pref("dom.w3c_touch_events.legacy_apis.enabled", true);

// Plenty of sites serve a desktop layout to anything claiming to be desktop,
// however narrow the viewport is.
user_pref("general.useragent.override", "@USER_AGENT@");

// Touch scrolling should feel like touch scrolling.
user_pref("apz.allow_zooming", true);
user_pref("dom.meta-viewport.enabled", true);

// --- keep the kiosk clean ---------------------------------------------------

user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("datareporting.healthreport.uploadEnabled", false);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.tabs.crashReporting.sendReport", false);
user_pref("app.update.auto", false);
user_pref("extensions.update.enabled", false);
user_pref("browser.newtabpage.activity-stream.feeds.telemetry", false);

// The disk is a tmpfs and the profile is thrown away on restart; do not spend
// time or memory on a cache that cannot outlive the session.
user_pref("browser.cache.disk.enable", false);
