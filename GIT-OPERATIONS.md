# Finplot Git 与运行规则

## 固定对象

- 远端：`git@github.com:xusz12/Finplot.git`（GitHub 私有仓库）
- 稳定版 v1：`/Users/x/.slock/agents/c131e49e-2176-40c0-9513-5bf482ab810c/billing-observatory`
  - 分支：`main`
  - 运行入口：真实账本只读实例，端口 `8766`
- 开发版 v2：`/Users/x/Finplot-dev`
  - 分支：`develop`
  - 验证入口：合成数据库实例，独立端口 `8775`
- 首次入库基线：`680745f`

当前目录、远端、分支和基线均以 Git 命令输出为准，不以目录名或记忆推断。

当前运行状态核对（2026-09-14）：端口 `8765` 仍由原稳定目录的合成演示实例占用，端口 `8766` 由原稳定目录的真实只读实例占用；`8775` 未监听。启动 v2 前仍须重新核对端口，不能误停 `8765` 的现有演示服务。

## 开发、审核与发布

1. 在 v2 的 `develop` 上开发，只使用合成数据库和独立端口；不得把真实账本、密钥、个人配置或敏感证据加入 Git。
2. 实现者完成自验后冻结精确候选 commit 和 diff；Checker 独立复核，用户验收后才可发布到 v1 `main`。
3. Git 管理员在操作前 fresh 检查 HEAD、远端、候选文件、工作区和用户已有修改；只提交批准范围，commit 信息使用简体中文。
4. 只有本轮明确授权时才 push；push 不自动触发稳定目录覆盖、重启或数据库迁移。

## v1 升级与回退

升级前记录旧 commit 和启动配置。仅当 v1 工作区 clean、分支无分叉且目标 commit 已验收时执行：

```sh
cd /Users/x/.slock/agents/c131e49e-2176-40c0-9513-5bf482ab810c/billing-observatory
git status --short --branch
git fetch origin main
git pull --ff-only origin main
```

然后按既有启动配置重启 `8766` 实例，并用只读冒烟检查确认页面、API 和真实账本均可用。不得修改真实数据库。

升级失败时停止新实例，记录失败证据，使用升级前记录的 `<old_sha>` 和原启动配置恢复：

```sh
git switch --detach <old_sha>
# 按原启动配置启动 8766 实例并完成只读冒烟检查
```

恢复过程不得 reset 覆盖本地改动；确认修复方案并重新验收后，再回到 `main` 发布。

## 常用复核命令

```sh
git status --short --branch
git remote -v
git log -1 --oneline --decorate
git ls-remote --heads origin
```
