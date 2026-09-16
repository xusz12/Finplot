# Finplot Git 与运行规则

## 固定对象

- 远端：`git@github.com:xusz12/Finplot.git`（GitHub 私有仓库）
- 唯一远端分支：`main`
- 正式目录：`/Users/x/Finplot`
  - 跟踪：`main`
  - 运行入口：真实账本只读实例，端口 `8766`
- 开发目录：`/Users/x/Finplot-dev`
  - 跟踪：`main`
  - 验证入口：合成数据库实例，独立端口 `8775`
- 当前发布标签：`v0.1.1`，指向已验收代码 `049f8b98124f00868bbc7ed98c9550a87a8190a0`

当前目录、远端、分支和基线均以 Git 命令输出为准，不以目录名或记忆推断。

运行状态必须在每次操作前重新核对；不得依据旧记录、目录名或端口约定判断当前进程，更不得擅自启动、停止或切换用户的实例。

## 开发、审核与发布

1. 只在 `/Users/x/Finplot-dev` 的 `main` 上开发、修复、commit 和 push；普通开发使用合成数据库和独立端口，不得把真实账本、密钥、个人配置或敏感证据加入 Git。
2. 实现者完成自验后冻结精确候选 commit 和 diff；Checker 独立复核，用户验收后才可 push 到远端 `main`。
3. Git 管理员在操作前 fresh 检查 HEAD、远端、候选文件、工作区和用户已有修改；只提交批准范围，commit 信息使用简体中文。
4. 只有本轮明确授权时才 push；push 不触发正式目录同步、重启或数据库迁移。
5. 版本标签不可移动或覆盖。`v0.1.1` 继续指向已验收代码 `049f8b98124f00868bbc7ed98c9550a87a8190a0`；其后的发布说明修正提交属于 `main` 文档历史，不倒改该标签。

## 正式目录升级与回退

正式目录何时升级完全由用户本人决定，agent 不得代为 pull 或同步。用户升级前记录旧 commit 和启动配置；仅当工作区 clean、分支无分叉且目标 commit 已验收时执行：

```sh
cd /Users/x/Finplot
git status --short --branch
git fetch origin main
git pull --ff-only origin main
```

然后由用户按既有启动配置手动启动 `8766` 实例，并用只读冒烟检查确认页面、API 和真实账本均可用。不得修改真实数据库。

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
