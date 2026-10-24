// On the login page: whatever keys a tab unlocked under an earlier login, now ended or
// expired, are forgotten (docs/m5-encryption-design.md §4). The name is keys.js's STORAGE.
sessionStorage.removeItem('footage-archive-keys');
