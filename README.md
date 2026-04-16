# 李鮮 Line Bot

## 環境變數（在 Render 設定）
| 變數 | 值 |
|------|-----|
| LINE_CHANNEL_SECRET | 18bb0d26fe46ef80d2b74d92f492a674 |
| LINE_CHANNEL_ACCESS_TOKEN | Emh1y0NPlkj...（完整token） |
| LINE_GROUP_ID | 群組ID（選填） |

## 群組訂單格式
```
王阿嬤 0912345678 台中市北屯區中清路100號 5斤2箱 10斤1箱
```

## 指令
- /list 或 今日訂單 → 查看今天的手動訂單
- /clear 或 清除 → 清除今天的手動訂單

## Webhook URL
部署後填入 LINE Developers：
https://你的render網址/callback
