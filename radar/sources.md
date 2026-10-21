# 信息源监控清单

每行一个信息源：`名称 | URL | 关注点`。雷达任务按此清单扫描，发现新条目即去重推送。

```
合作方公告 | https://partner.example.com/news | 产品更新、维护窗口
行业周报   | https://weekly.example.org/     | 本周要闻
依赖发布   | https://releases.example.dev/    | 新版本、安全公告
```

> 首次运行会把清单里已存在的当前条目灌入 `radar/state.json` 的 seen，避免把存量当新增狂推。
