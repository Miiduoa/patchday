# Patchday｜SQLite migration 預演

在修改資料庫前，先把 migration 跑在拋棄式副本上，查看 schema 差異、資料筆數變化、外鍵與完整性檢查。

[執行方式](../README.md) · [範例報告](https://miiduoa.github.io/patchday/) · [設計取捨](design.md)

## 實際解決的問題

SQL 語法正確，不代表套用到現有資料時會成功。例如有資料的表新增沒有預設值的 NOT NULL 欄位，或延遲外鍵到交易結束才檢查。Patchday 使用現有資料的 snapshot 預演這些情況。

原始資料庫以唯讀連線開啟，backup API 產生記憶體副本，整批 migration 在同一個交易執行。任何一個檔案失敗，就回滾整批副本變更。工具沒有正式套用的指令。

## 三段演示

1. 執行範例的 `001_due_dates.sql`、`002_activity.sql`，觀察新增欄位、索引、trigger 與 activity 表。
2. 換成 `examples/broken.sql`，觀察 NOT NULL 失敗與批次回滾。
3. 執行 `003_cleanup.sql`，觀察 SQL 成功但因筆數減少而回傳 Review／exit 3。

## 適合深入討論的資訊議題

- 為什麼不能直接複製有 WAL 的主資料庫檔案？
- 即時與延遲外鍵約束，分別在哪個時間點失敗？
- 為什麼整批 migration 應由同一層管理交易？
- SQLite authorizer 如何阻止 ATTACH 與自行 COMMIT？它又不是哪些威脅的防線？
- 為什麼相同筆數不能證明資料沒有損失？

目前的驗證包括 23 項測試，以及可重建的合成資料與報告。這是資料庫工具原型，沒有宣稱已在正式環境部署；記憶體副本的耗時也不能當成 production benchmark。
