// auth-guard.js
(function () {
    const token = localStorage.getItem('deer_token');
    if (!token) {
        window.location.href = 'login.html';
    }
})();

function logoutSystem() {
    localStorage.clear();
    window.location.href = 'login.html';
}
