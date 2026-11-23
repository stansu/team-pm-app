# Team PM — 團隊專案排程（雲端版 ＋ 自架本機版）

一個零外部套件、單檔就能跑的團隊專案排程工具：甘特圖（日／週／月／季／年五段縮放）、看板、清單、進度分析、休假日截斷、四級權限、深色主題。

這個倉庫提供**兩種使用方式**，資料格式相同、可互相切換：

| 方式 | 檔案 | 需要什麼 | 適合 |
| --- | --- | --- | --- |
| **雲端版**（免架站） | `index.html` | 瀏覽器＋一個 GitHub 私有倉庫 | 外出、手機、跨地點協作 |
| **本機版**（自己架） | `pm_server-v11.py` | 電腦上有 Python 3.8+ | 公司區網、資料不出內網 |

---

## 一、雲端版（GitHub Pages）

1. 開啟 <https://stansu.github.io/team-pm-app/>。
2. 首次連線兩種填法：
   - 貼上管理員提供的 `PMGH:...` 快速連線碼 → 按「解析填入」。
   - 或手動填入 GitHub 帳號、私有倉庫名稱、分支、資料路徑與 Fine-grained Token。
3. 按「連線並載入專案資料」，再輸入團隊觀看密碼（首次預設 `8888`）。
4. 設定會存在該裝置瀏覽器，之後不必重填。

> 這個倉庫的 `index.html` **不含任何專案資料、密碼或 Token**，可以公開；實際資料放在你自己的私有倉庫。

---

## 二、本機版（自己架區網伺服器）

適合「公司不能連外網」或「資料不想離開內網」的情境。單一 Python 檔、不裝任何套件、不寫系統機碼。

### 1. 下載

- 直接下載：<https://github.com/stansu/team-pm-app/raw/main/pm_server-v11.py>
- 或命令列：

```bash
curl -LO https://raw.githubusercontent.com/stansu/team-pm-app/main/pm_server-v11.py
```

### 2. 啟動

```bash
python3 pm_server-v11.py                 # 預設 8765 埠
python3 pm_server-v11.py 9000            # 改用 9000 埠
python3 pm_server-v11.py --shared /srv/共用   # 額外開放共用資料夾
python3 pm_server-v11.py --reset-admin   # 忘記密碼時重設（PIN→1234、觀看密碼→8888）
```

Windows 把 `python3` 換成 `python`。有執行權限的話也可以 `./pm_server-v11.py`。

啟動後終端機會印出：

```
本機開啟: http://127.0.0.1:8765
區網 IP : http://192.168.x.x:8765
資料位置: .../project_data.json
```

### 3. 使用

- 自己開 `http://127.0.0.1:8765`，同事開 `http://<你的區網IP>:8765`。
- 觀看密碼預設 **8888**，系統管理員 PIN 預設 **1234**（登入後在「設定」修改）。
- 資料存在腳本旁邊的 `project_data.json`，每天自動在 `backups/` 留一份快照。

### 4. 讓別人連得到

- **防火牆**：Windows 首次啟動會跳「允許存取」；Linux（firewalld）需要
  `sudo firewall-cmd --add-port=8765/tcp --permanent && sudo firewall-cmd --reload`
- 同事要跟你在**同一個區網**（或 VPN），用手機熱點分享的網路通常也可以。
- 埠被占用時程式會自動往後找（最多 +20），啟動畫面會顯示實際埠號。

### 5. 選用：接上 GitHub 私有倉庫做雙向同步

想「公司本機」與「雲端版」共用同一份資料時：

1. GitHub → Settings → Developer settings → **Fine-grained token**，只勾你的私有資料倉庫、權限 `Contents: Read and write`。
2. 啟動本機版 → 右上角「系統設定 → ☁️ 雲端同步 (GitHub)」→ 填入帳號、倉庫、分支、Token → 儲存並測試。
3. 之後本機存檔約 1 秒內自動推上 GitHub；雲端修改約 10 秒內自動拉回，拉回前會在 `backups/` 留快照。

### 6. 需求

- **Python 3.8 以上**，只用標準庫，**不需要** `pip install`。
- 磁碟空間：程式 330 KB 左右，資料依專案量而定。

---

## 安全提醒

- Fine-grained Token 只需 **單一私有倉庫** 的 `Contents: Read and write` 權限。
- Token 只存在你自己的瀏覽器（雲端版）或 `github_sync.json`（本機版），不會上傳到這個公開倉庫。
- 若 Token 外流，請立即到 GitHub → Settings → Developer settings 撤銷並重新產生。
- 本機版的 `project_data.json` 含全部專案內容，要分享給別人前請自行判斷。

## 授權

本專案採用 [MIT License](LICENSE)：可以自由使用、修改、散布（含商業用途與閉源專案），
唯一條件是保留著作權聲明與授權全文。軟體不提供任何保證。

## 版本

- `pm_server-v11.py`：本機版（含 GitHub 私有倉庫雙向同步、五段時間縮放、休假日截斷、四級權限、深色主題）
- `index.html`：雲端版（同功能的前端，資料直接走 GitHub API）
