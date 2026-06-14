importScripts('https://www.gstatic.com/firebasejs/10.8.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.8.1/firebase-messaging-compat.js');

firebase.initializeApp({
    apiKey: "AIzaSyBLXut5CS5VfaKXCvERMq0Y04RcyurQarc",
    authDomain: "login-sholat.firebaseapp.com",
    projectId: "login-sholat",
    storageBucket: "login-sholat.firebasestorage.app",
    messagingSenderId: "834035877874",
    appId: "1:834035877874:web:bb2a0ecf3621ba546b8d6b"
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage(function (payload) {
    const data = payload.notification || payload.data || {};
    const title = data.title || 'Checklist Sholat';
    const options = {
        body: data.body || 'Waktunya sholat!',
        icon: '/Reve.png',
        badge: '/Reve.png',
        vibrate: [200, 100, 200],
        tag: 'sholat-reminder',
        renotify: true,
        actions: [
            { action: 'open', title: 'Buka App' }
        ]
    };
    self.registration.showNotification(title, options);
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientList) {
            for (const client of clientList) {
                if (client.url.includes('/index.html') || client.url.endsWith('/')) {
                    return client.focus();
                }
            }
            return clients.openWindow('/');
        })
    );
});
