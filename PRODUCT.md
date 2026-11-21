# TC 的食事筆記
<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
TC 與朋友：住在日本或赴日旅行的台灣人，使用繁體中文及日文，在手機與電腦查找朋友提過的餐廳。

## Product Purpose
Discord bot 將朋友提到的餐廳（多為 Tabelog 連結）存入 SQLite；網站提供搜尋、查看完整資訊與評論、地圖及管理編輯。

## Capabilities and Constraints
沿用 FastAPI、靜態 HTML/CSS/JavaScript 與既有 API；約 53 間店。Google My Maps 沿用 iframe，店家沒有座標。正式資料庫、uploads 與環境檔不得變動。無新套件、無字體 CDN。管理使用 session cookie；公開資訊不揭露 Discord IDs。正式評論來自 restaurant_comments，舊 comments 原文保留。

## Brand Commitments
紙本食事筆記、喫茶店菜單與獨立飲食雜誌；暖紙、墨色、克制朱紅、明朝標題、無襯線內文。四個頁面使用同一個世界。禁止 SaaS 儀表板、玻璃模糊、大漸層、浮起卡片、表情圖示及口號 hero。

## Accessibility & Inclusion
AA 對比、44px 觸控目標、鍵盤操作、可見焦點、360px 響應式、中日切換、載入與錯誤回復。
