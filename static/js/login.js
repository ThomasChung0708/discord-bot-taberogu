
    document.getElementById("loginForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = document.getElementById("loginMessage");
      try {
        const response = await fetch("/api/admin/login", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({password: document.getElementById("password").value})
        });
        if (response.ok) { window.location.replace("/admin"); return; }
        const data = await response.json();
        message.textContent = typeof data.detail === "string" ? data.detail : "登入失敗";
      } catch (error) { message.textContent = "連線失敗，請稍後再試。"; }
    });
  