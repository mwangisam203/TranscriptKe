"use strict";
(() => {
  // Replace null with the HTTPS URL of an account you actually control.
  // These sample handles are design placeholders, not registered accounts.
  const profiles = [
    {name: "LinkedIn", mark: "in", handle: "transcriptske-demo", url: null, platform: "https://www.linkedin.com/"},
    {name: "Instagram", mark: "IG", handle: "@transcriptske_demo", url: null, platform: "https://www.instagram.com/"},
    {name: "Facebook", mark: "f", handle: "TranscriptsKE Demo", url: null, platform: "https://www.facebook.com/"},
    {name: "X", mark: "X", handle: "@transcriptske_demo", url: null, platform: "https://x.com/"}
  ];
  const container = document.getElementById("social-links");
  if (!container) return;
  let demos = 0;
  for (const profile of profiles) {
    let verifiedURL = null;
    try {
      const parsed = new URL(profile.url);
      if (parsed.protocol === "https:" && !parsed.username && !parsed.password) verifiedURL = parsed.href;
    } catch { /* Unconfigured profiles link to the platform homepage. */ }
    if (!verifiedURL) demos++;
    const link = document.createElement("a");
    link.href = verifiedURL || profile.platform; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", verifiedURL ? `TranscriptsKE on ${profile.name} (opens in a new tab)` : `Explore ${profile.name} (demo handle; opens platform in a new tab)`);
    const mark = document.createElement("span"); mark.className = "social-mark"; mark.textContent = profile.mark; mark.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    const name = document.createElement("strong"); name.textContent = `${profile.name} ↗`;
    const handle = document.createElement("small"); handle.textContent = verifiedURL ? "Visit our profile" : `Demo: ${profile.handle}`;
    text.append(name, handle); link.append(mark, text); container.append(link);
  }
  document.getElementById("social-demo-note").textContent = demos ? "Sample handles for design preview. These are not registered TranscriptsKE accounts; demo links open each social platform." : "Follow our accounts for updates. Never share passwords, email codes or academic records in social messages.";
})();
