# AI Discord 聊天機器人

## 專案簡介
這是一個功能豐富的 Discord 機器人，旨在將 OpenAI 的 ChatGPT 模型整合到您的 Discord 伺服器中，提供智能對話功能。除了核心的 AI 對話能力，本機器人還包含一系列管理指令，允許使用者自訂對話行為、管理監聽頻道，並提供機器人本身的運行狀態監控。專案採用模組化設計（使用 `cogs`）並具備資料庫持久化功能，以儲存使用者設定和對話歷史，確保機器人的穩定運行與高度客製化。

## 主要功能

### 智能 AI 對話
* **私訊對話：** 機器人可在私訊中與使用者進行一對一的 AI 對話。
* **頻道監聽：** 可設定機器人在特定文字頻道中監聽訊息並自動回應。
* **上下文記憶：** 支援對話歷史紀錄，AI 能記住之前的對話內容，提供更連貫的交流（可由使用者啟用/停用）。
* **個人化系統提示：** 使用者可設定自訂的「系統提示」（system prompt），引導 AI 的對話風格或行為。
* **模型選擇：** 支援多種 OpenAI 模型，例如 `gpt-4o`、`gpt-4-turbo`、`gpt-4` 和 `gpt-3.5-turbo`。

### 使用者設定管理
* `/settings`: 允許使用者查看和修改其個人化的 AI 對話設定，包括偏好的 AI 模型、是否記住對話上下文，以及自訂的系統提示。
* `/clear_my_chat_history`: 使用者可以清除自己儲存在資料庫中的所有對話歷史。

### 頻道管理指令 (僅限管理員)
* `/channel register`: 管理員可將目前所在的頻道註冊為 AI 監聽頻道。
* `/channel unregister`: 管理員可將頻道從 AI 監聽列表中移除。
* `/channel list`: 列出目前伺服器中所有被設定為 AI 監聽的頻道。

### 機器人狀態與維護
* `/ping`: 顯示機器人目前的延遲（ping 值）。
* `/status`: 提供機器人的詳細運行狀態報告，包括運行時間、延遲、所在伺服器數量等。
* `/sync_commands` (僅限擁有者): 手動同步 Discord 斜線指令。
* `!load`, `!unload`, `!reload` (僅限擁有者): 動態載入、卸載和重新載入機器人功能模組 (cogs)。
* `!restart` (僅限擁有者): 重啟機器人。
* `!stop` (僅限擁有者): 關閉機器人。

## 技術棧

* **程式語言：** Python
* **Discord 框架：** `discord.py`
* **AI 整合：** `openai` 函式庫
* **環境變數管理：** `python-dotenv`
* **資料庫：** `sqlite3` (內建於 Python)，用於儲存使用者設定和對話歷史。

## 安裝與設定

### 前置條件
* Python 3.8+
* Discord 帳戶與機器人應用程式 (請確保您的機器人擁有必要的權限，例如 `message_content` intent)。
* OpenAI API Key

### 設定步驟
1.  **複製專案：**
    ```bash
    git clone [https://github.com/ldn970110/AI-DiscordBot.git](https://github.com/ldn970110/AI-DiscordBot.git)
    cd AI-DiscordBot
    ```
2.  **建立虛擬環境 (推薦)：**
    ```bash
    python -m venv venv
    source venv/bin/activate  # macOS/Linux
    .\venv\Scripts\activate   # Windows
    ```
3.  **安裝依賴：**
    ```bash
    pip install -r requirements.txt
    ```
4.  **配置環境變數：**
    * 在專案根目錄下建立一個 `.env` 檔案。
    * 填入您的 Discord Bot Token 和 OpenAI API Key：
        ```
        DISCORD_BOT_TOKEN=您的Discord機器人Token
        OPENAI_API_KEY=您的OpenAIAPIKey
        ```
5.  **配置 `config.json` (可選)：**
    * 您可以修改 `config.json` 中的預設系統提示、允許的 OpenAI 模型列表等設定。

### 運行機器人
```bash
python bot.py
