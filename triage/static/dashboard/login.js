"use strict";
document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("sign-in");
  const error = document.getElementById("login-error");
  button.disabled = true; error.textContent = "";
  try {
    const response = await fetch("/api/dashboard/login", {method: "POST", headers: {"Content-Type": "application/json", "X-Sgnlol-Request": "dashboard"}, credentials: "same-origin", body: JSON.stringify({username: document.getElementById("username").value.trim(), password: document.getElementById("password").value})});
    if (!response.ok) throw new Error(response.status === 429 ? "Too many attempts. Please wait one minute." : response.status === 401 ? "Incorrect username or password." : "Sign-in unavailable. Please try again.");
    document.getElementById("password").value = "";
    window.location.replace("/dashboard" + window.location.hash);
  } catch (err) { error.textContent = err.message; }
  finally { button.disabled = false; }
});
