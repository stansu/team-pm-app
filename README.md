# Team PM — 團隊專案排程（雲端版 ＋ 自架本機版）

一個零外部套件、單檔就能跑的團隊專案排程工具：甘特圖（日／週／月／季／年五段縮放）、看板、清單、進度分析、休假日截斷、四級權限、深色主題、**手機版面（可直接用手指拖曳任務條）**。

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

## 三、施工監理日報（獨立模組）

本機版內建一個獨立的日報模組，適合工地每天回報：出工、機具、工項數量、材料進場、查驗、缺失改善與現場照片。

- **入口**：排程頁右上角「📋 監理日報」，或直接開 `http://<伺服器IP>:8765/report`
- **資料**：存在 `report_data.json`（與排程的 `project_data.json` 分開，壞了不會影響排程），每天自動備份到 `backups/report_data_日期.json`
- **權限**：沿用同一組登入與觀看密碼；「唯讀檢視」只能看，日報由建立者或專案管理員才能修改／刪除
- **照片**：直接從共用資料夾挑選，不經過上傳，也不存進 JSON（避免拖慢同步）
- **列印**：右上角「列印」輸出 A4 版面，含表頭、各項紀錄、照片與填寫人／工地主任／監造簽名欄
- **快速填寫**：新日報按「沿用上一份」可帶入上一次的人力、機具、工項

限制（目前版本）：照片上限 40 張／份、沒有手機直接拍照上傳、沒有線上簽核流程。

### 雲端版（GitHub Pages）

同一套表單也有一份純前端版本，資料直接寫回你的私有 repo，不需要開電腦：

- **網址**：<https://stansu.github.io/team-pm-app/report/>（把 `report/index.html` 推上 main 後約 1 分鐘生效）
- **第一次開啟**：貼上 `PMGH:...` 快速連線碼，或手動填 owner／repo／branch／token（跟雲端版排程用同一個私有資料 repo）
- **資料位置**：日報本文存 `report_data.json`；照片存 `reports/photos/<日期>/<檔名>.jpg`
- **照片壓縮**：在手機上先縮圖再上傳，可選
  - 省空間 1024px／q0.6（約 60–100 KB）
  - 標準 1280px／q0.7（約 120–180 KB）
  - 高畫質 1920px／q0.8（約 250–450 KB）
- **容量估算**：一天 10 張用「標準」約 1.5 MB／天（≈30 MB／月）。日報 JSON 每次儲存都是新版本，但 Git 會做差異壓縮，100 份日報的本文也才約 200 KB，真正的成長來源是照片。
- **注意**：iPhone 的 HEIC 請把相機格式改成「最相容」或先轉 JPEG；刪除日報不會自動刪照片檔（可到 repo 手動清）；雲端版與本機版是不同的資料檔，不會互相同步。

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
- `report.html`：施工監理日報頁面（由 `pm_server-v11.py` 在 `/report` 提供）
- `index.html`：雲端版（同功能的前端，資料直接走 GitHub API）
- `report/index.html`：雲端版施工監理日報（GitHub Pages 用）
