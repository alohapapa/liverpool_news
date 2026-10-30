/* 휴대폰 알림을 받아서 보여주는 작은 프로그램 (서비스 워커).
   사이트를 열지 않아도 알림이 오면 이 파일이 깨어나 알림을 띄우고, 알림을 누르면 해당 화면을 연다. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch (e) { msg = { body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(msg.title || "리버풀 소식", {
    body: msg.body || "",
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: msg.tag,
    data: { url: msg.url || "./" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data.url || "./", self.registration.scope).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if (w.url.startsWith(self.registration.scope) && "focus" in w) {
        await w.focus();
        if ("navigate" in w) return w.navigate(url);
        return;
      }
    }
    return self.clients.openWindow(url);
  })());
});
