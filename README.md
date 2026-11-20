# Team PM Cloud — GitHub Pages 前端

團隊專案排程系統的 **GitHub Pages 雲端版**（純靜態單頁）。

- 這個倉庫只放 `index.html`，**不含任何專案資料、密碼或 Token**，可以公開。
- 所有專案與任務資料都放在另一個 **私有** 倉庫（預設 `team-pm-data`），由瀏覽器透過 GitHub API 直接讀寫。
- 本機版伺服器：`pm_server-v11.py`（放在私有倉庫，或公司電腦的專案資料夾內）。

## 使用方式

1. 開啟 <https://stansu.github.io/team-pm-app/>。
2. 首次連線有兩種填法：
   - 貼上管理員提供的 `PMGH:...` 快速連線碼 → 按「解析填入」。
   - 或手動填入 GitHub 帳號、私有倉庫名稱、分支、資料路徑與 Fine-grained Token。
3. 按「連線並載入專案資料」，再輸入團隊觀看密碼（首次預設 `8888`）。
4. 設定會存在該裝置瀏覽器，之後不必重填。

## 安全提醒

- Fine-grained Token 只需 **單一私有倉庫** 的 `Contents: Read and write` 權限。
- Token 只存在使用者瀏覽器本機（以及公司電腦的 `github_sync.json`），不會上傳到這個公開倉庫。
- 若 Token 外流，請立即到 GitHub → Settings → Developer settings 撤銷並重新產生。
