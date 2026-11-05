七圣召唤本地增强版 · 开发交接包
================================

这不是便携版，而是给接手的开发者/Agent 的完整开发快照。

包含
----
- 完整 Git 仓库：.git、分支 local、remote upstream
  （https://github.com/piovium/genius-invokation.git）
- 预构建前端：packages/standalone/dist
- 可分发便携包：dist-portable/GeniusInvokation-Portable.zip
- 源码、交接报告 HANDOVER-REPORT.md、local/ 下的全部文档

不包含
------
- node_modules：约 880 MB，含 3401 个符号链接，不能跨机器复制
- local/.cache、local/_screenshots、其他临时产物

第一次打开
----------
1. 确认环境（只有改代码/构建才需要；只运行已构建的 app 不需要）：
     node -v
     pnpm -v
   推荐 Node 24 或 26，pnpm 12.13.0。

2. 直接启动（dist 已预构建，不需要先 pnpm install）：
     Start.cmd
   或：
     node local\server.mjs
   浏览器打开 http://127.0.0.1:7717/

3. 改代码后重新构建：
     pnpm install --frozen-lockfile
     pnpm build "standalone..."
     node local\server.mjs

4. 确认 Git 正常：
     git status
     git log --oneline
     git remote -v
   应看到分支 local 和 upstream remote。

5. 跟进上游：
     local\update-upstream.cmd --check
     local\update-upstream.cmd

Windows 说明
------------
- 上游的 packages/server/public 和 packages/typings/proto 是符号链接。
  为了能在 Windows 上正常解压/复制，本包把它们换成了真实目录，并提交为
  "handoff: Windows-friendly source package layout"。
- 只开发 standalone 不需要 packages/server/public。
- 如果你重新 git clone 上游，这两个链接在 Windows 上可能 checkout 失败；
  直接用本包即可。

Node 版本说明
-------------
- package.json 声明 engines.node ^26.12.1；Node 24.19.0 实测可以
  install/build/run，pnpm 只会 warning，不阻塞。
- 要完全匹配声明，用 Node 26.12.1。

安全
----
- 服务只监听 127.0.0.1，没有鉴权，不要暴露公网。
