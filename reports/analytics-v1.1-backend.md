# 基础分析 v1.1 后端验证报告

日期：2026-09-13

分支：`backend-v1.1-analytics`

修复提交：`00f8744`
范围：task #9 后端接口、聚合口径、只读安全回归与合成库契约测试

## 交付内容

- 新增 `GET /api/analytics`；既有 `/api/dashboard` 和 `/api/version` 保持兼容。
- 统一返回上海时区的 `start`/`end_exclusive`，请求日期的 `end` 和自选对比日期仍是包含端。
- 在一个 `mode=ro` + `query_only` SQLite 快照中计算当前期、对比期、分类并集及趋势，返回同一 `version`。
- 覆盖性质拆分、日常结余率、已实现投资损益、分类金额/占比/频次/均笔、分期及累计盈余、日均支出、前三个完整自然月加权参照、近 12 个月概览和月度支出日历。
- 零基数、负盈余、未来分期、账本边界覆盖和比较不可用均显式表达；不返回无穷大比例。
- 完整自然周期和自选/单日/未完成周期使用不同的去年同期边界算法，双向闰年均已覆盖。
- 支出日历的 `transaction_count` 只统计符合筛选的支出交易，与支出明细一致；自选范围即使请求 `period_mode=elapsed` 也保留显式 `[start,end)`，自然周期才按今天截断。

## 验证

所有写入、删除和版本变化均针对每个测试独立创建的临时合成 SQLite；没有使用真实账本做变更测试，也没有修改运行中的 8765/8766 实例。

```text
python3 -m py_compile backend/app/main.py                         pass
uv run --with fastapi --with httpx --with 'uvicorn[standard]' \
  python -m unittest discover -s tests -v                          18/18 pass
.../.venv/bin/pip check                                            No broken requirements found
node --check frontend/app.js                                      pass
HTTP smoke on 127.0.0.1:8774                                     1.1 / 2026-08-01..2026-09-01 / 1299 cents / 31 points
```

测试覆盖：

- 2024-02-28 单日、2024-02-29 单日、完整 2024-02，以及完整 2025-02 反向映射到闰年 2024-02；
- elapsed/full 当前月、当前月已过天数、短月与不同期长提示；
- 零对比基数、负盈余/金额状态、空比较、未来分期空值；
- 性质和总额守恒、分类两期并集及差额守恒、Top N/其他结构；
- 标签任一去重、自然日分母、前三月加权日均、月历金额守恒；
- 同一版本校验和数据变化后的 `409`。
- 支出日历收入-only 日期不计入支出笔数；自选范围跨今天/未来日期的 elapsed/full 边界保持显式结束日。

## 边界与集成说明

本提交只负责后端和测试，未切换正在运行的 8765/8766，也未读取或导出真实交易正文。前端集成应使用趋势分期自带的实际 `start`/`end_exclusive` 请求明细；均值与日均的十进制展示字段应配合 numerator/denominator 字段，不应交给整数 `BigInt` 金额格式器。真实账本只读核对和浏览器端 v1.1 验证需在前后端集成提交后由审核者继续完成。
