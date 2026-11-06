# 新手晨報：資料收集

這個 repo 每天台北時間 05:30（週一到週六）自動抓市場資料，存成 `data/latest.json`。
06:30 前，Claude 會讀這個檔案、寫白話分析，更新晨報頁面。

## 設定步驟（只要做一次，約 10 分鐘）

### 1. 建立 repo
1. 登入 GitHub，打開 <https://github.com/new>
2. **Repository name** 填 `newbie-morning-report`
3. 選 **Public**（公開，Claude 才讀得到資料；裡面只有公開的市場數字）
4. 其他都不要勾，按 **Create repository**

### 2. 上傳程式
1. 在剛建好的頁面，點 **uploading an existing file** 這個連結
2. 把這個資料夾裡的 `collect.py`、`requirements.txt`、`README.md` 三個檔案拖進去
3. 按最下面的 **Commit changes**

### 3. 建立排程檔（這個要用貼的）
1. 回到 repo 首頁，按 **Add file → Create new file**
2. 檔名欄位輸入：`.github/workflows/collect.yml`
   （打到 `/` 時 GitHub 會自動變成資料夾，這是正常的）
3. 用記事本打開這個資料夾裡的 `.github\workflows\collect.yml`，全部複製，貼到 GitHub 的編輯框
4. 按 **Commit changes...**，再按一次 **Commit changes**

### 4. 手動跑第一次
1. 點 repo 上方的 **Actions** 分頁
2. 左邊點 **每日抓資料**，右邊按 **Run workflow → Run workflow**
3. 第一次要從證交所補約 140 天的個股資料，大約跑 **8～10 分鐘**；之後每天只要 1～2 分鐘
4. 出現綠色勾勾就成功了。回到 repo 首頁，應該會看到多了一個 `data` 資料夾

### 5. 告訴 Claude 你的 GitHub 帳號名稱
例如你的 repo 網址是 `https://github.com/abc123/newbie-morning-report`，帳號名稱就是 `abc123`。

## 如果出現紅色叉叉
- 點進那次執行，看最後幾行錯誤訊息，整段複製給 Claude
- 如果訊息有 `Permission denied` 或 `403`：到 **Settings → Actions → General**，最下面 **Workflow permissions** 選 **Read and write permissions**，按 Save，再跑一次
- 某個資料來源沒抓到不會讓整個流程失敗，只會記在 `latest.json` 的 `errors` 欄位裡

## 檔案說明
| 檔案 | 做什麼 |
|---|---|
| `collect.py` | 抓資料、算指標的 Python 程式 |
| `requirements.txt` | 程式需要的套件 |
| `.github/workflows/collect.yml` | 告訴 GitHub 每天幾點跑 |
| `data/latest.json` | 最新一天的資料（自動產生） |
| `data/history/` | 每天的資料備份（自動產生） |
| `data/twse_closes.csv.gz` | 上市個股近 140 天收盤價，用來算均線（自動產生） |

## 資料來源
Yahoo Finance（yfinance）、臺灣證券交易所、臺灣期貨交易所。
多空排列比例只用上市普通股計算（短：5/10/20 日均線；長：收盤/60/120 日均線），
跟原始驗證報告用的 TEJ 口徑不完全相同。
