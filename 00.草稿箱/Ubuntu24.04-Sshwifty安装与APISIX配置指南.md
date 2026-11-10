# Ubuntu 24.04：Sshwifty 安装与 APISIX 配置指南

## 1. 用途与当前环境

Sshwifty 是浏览器 SSH 客户端。浏览器连接 Sshwifty，由 Sshwifty 服务端连接目标服务器的 SSH 服务。

| 项目 | 本次环境 |
|---|---|
| 操作系统 | Ubuntu 24.04 |
| Sshwifty 部署主机 | 10.157.127.3（nd03） |
| Sshwifty 宿主机监听 | 0.0.0.0:8182，HTTP |
| APISIX 部署主机 | 10.157.64.3 |
| 公网已有映射 | 211.143.159.218:8223 → 10.157.64.3:9080 |
| 网页入口 | `/ssh`、`/ssh/` |
| APISIX 路由名称 | sshwifty-entry |
| 网页服务上游 | http://10.157.127.3:8182 |

`0.0.0.0` 表示监听所有 IPv4 网卡，不是用户应填写的访问地址。

本文中的 Ubuntu 安装命令按 root 用户执行，普通用户需要在相应命令前使用 sudo。不要在 Mac 上执行 Ubuntu 安装命令。

### 已验证与待完成事项

- 排查时已确认容器启动成功，Sshwifty 监听 8182。
- 原 APISIX 路由已合并入口、资源与后端接口路径，未要求新增 SSH 路由。
- 排查时页面、引用脚本返回 200；未提供入口密码时，认证查询返回 403 并带认证挑战。
- HTTP 登录出现 `importKey` 错误，尚不能视为终端功能验收通过。
- 下文 HTTPS 和证书安装部分是后续操作方案，不代表已在服务器完成部署。

## 2. 安装前提

- 通过其他设备或服务器控制台进入 10.157.127.3，具有安装权限。
- APISIX 所在网络可以访问 10.157.127.3:8182。
- Sshwifty 容器可以连接目标服务器的 SSH 端口。
- 最终浏览器入口应使用受信任的 HTTPS；普通公网 HTTP 无法满足当前原版 Sshwifty 的浏览器加密 API 要求。
- 8182 应只允许可信内网及 APISIX 访问。Docker 发布端口时，还需核实实际网络访问策略，不能仅根据 UFW 状态判断隔离是否生效。

## 3. 在 10.157.127.3 安装 Docker

```bash
apt update
apt install -y docker.io
systemctl enable --now docker
docker version
```

如果 Docker 已安装并正常工作，无需重复安装。

## 4. 拉取镜像

优先使用项目发布的镜像：

```bash
docker pull niruix/sshwifty:latest
```

中国大陆网络无法拉取时，可以尝试第三方“毫秒镜像”加速地址：

```bash
docker pull docker.1ms.run/niruix/sshwifty:latest
docker tag docker.1ms.run/niruix/sshwifty:latest niruix/sshwifty:latest
```

第三方加速源的可用性和缓存版本可能变化，不能保证始终可用。需要固定版本时，应记录验证过的标签或镜像摘要；不要在未验证时自动更新 latest。

### 离线导入备用方式

在能访问 Docker Hub 的另一台同架构 Linux 机器执行：

```bash
docker pull niruix/sshwifty:latest
docker save -o sshwifty.tar niruix/sshwifty:latest
```

将文件传到目标服务器后执行：

```bash
docker load -i sshwifty.tar
```

ARM Mac 上下载的默认镜像架构可能与 x86_64 服务器不同，不能直接假设可以通用。

## 5. 首次启动 Sshwifty

### 设置网页入口密码

以下密码是 Sshwifty 网页入口密码，与 APISIX 管理密钥、SSH 账号密码不同。不要把实际密码写入本文档。

在 root 的 Bash 终端中执行，交互输入密码以避免把密码直接写入命令历史：

```bash
install -d -m 700 /etc/sshwifty
```

首次创建密码文件；如果已存在，先确认是否需要修改，不要直接覆盖：

```bash
(
  set -e
  umask 077
  if [ -e /etc/sshwifty/sshwifty.env ]; then
    printf '密码文件已存在，请先检查，未覆盖。\n' >&2
    exit 1
  fi
  read -r -s -p '输入 Sshwifty 网页入口密码: ' sshwifty_pass
  printf '\n'
  if [ -z "$sshwifty_pass" ]; then
    printf '密码不能为空。\n' >&2
    exit 1
  fi
  printf 'SSHWIFTY_SHAREDKEY=%s\n' "$sshwifty_pass" > /etc/sshwifty/sshwifty.env
  unset sshwifty_pass
)
```

### 启动容器

先确认没有已有同名容器：

```bash
docker ps -a --filter name=sshwifty
```

仅首次部署时执行：

```bash
docker run -d \
  --name sshwifty \
  --restart unless-stopped \
  -p 0.0.0.0:8182:8182 \
  --add-host host.docker.internal:host-gateway \
  --env-file /etc/sshwifty/sshwifty.env \
  -e SSHWIFTY_LISTENINTERFACE=0.0.0.0 \
  -e SSHWIFTY_LISTENPORT=8182 \
  niruix/sshwifty:latest
```

已有容器正在运行时，不要为了照抄步骤删除重建。环境变量或 env 文件内容的变化不会通过简单的 docker restart 自动更新到已有容器；如需变更，应先记录当前启动配置，再在维护时按更新后的参数重建容器。

## 6. 验证 Sshwifty 服务

```bash
docker ps --filter name=sshwifty
docker logs --tail 50 sshwifty
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8182/
```

预期容器处于 Up 状态，日志出现：

```text
Server (0.0.0.0:8182): Serving
```

没有配置文件时，程序可能先记录“找不到默认配置文件”，再从环境变量加载配置。后续正常出现 Serving 时，这条警告本身不表示启动失败。

不要只用 `curl -I` 判断健康状态：它发送 HEAD 请求，本次部署对此返回 501；应使用上述 GET 请求检查。

## 7. APISIX：只维护一条现有路由

在 APISIX 的上游配置中使用：

| 配置项 | 值 |
|---|---|
| 名称 | sshwifty-upstream |
| 协议 | http |
| 节点 | 10.157.127.3 |
| 端口 | 8182 |
| 权重 | 1 |
| 负载均衡 | roundrobin |
| Host 传递 | pass |

编辑现有 `sshwifty-entry` 路由：

| 配置项 | 值 |
|---|---|
| 路径，分别添加四项 | `/ssh`、`/ssh/`、`/sshwifty`、`/sshwifty/*` |
| 上游 | sshwifty-upstream |
| 请求方法 | 不限制；界面必选时选择全部 |
| WebSocket | 开启 |
| 状态 | 启用 |
| Host 匹配 | 本次按 IP 访问，不新增与实际请求不符的域名限制 |

UI 中填写路径时不要输入反引号、引号或 Markdown 标记。曾经误填成带反引号的 `/ssh/`，导致路由无法匹配。

### 路径重写插件

在该路由的 `proxy-rewrite` 插件中使用：

```json
{
  "regex_uri": ["^/ssh/?$", "/"]
}
```

删除之前设置的固定 `uri: "/"`，不要同时保留；固定 uri 的优先级更高，会把资源和接口请求也重写成首页。

预期转发行为：

```text
/ssh                       → /
/ssh/                      → /
/sshwifty/assets/...       → /sshwifty/assets/...
/sshwifty/socket/verify    → /sshwifty/socket/verify
/sshwifty/socket           → /sshwifty/socket
```

Sshwifty 官方不推荐部署到共享域名子路径。本方案将 `/ssh` 作为入口，但其资源和接口仍占用同一来源的 `/sshwifty` 前缀，不能把所有请求都限制在 `/ssh` 下。该前缀不能分配给其他应用。

## 8. 验证网关与认证接口

在 APISIX 所在网络检查后端连通性：

```bash
curl --connect-timeout 5 -sS -o /dev/null \
  -w 'HTTP %{http_code}\n' http://10.157.127.3:8182/
```

在 HTTPS 配置前，可使用现有 HTTP 入口验证路由，但不要把这视为可正常登录的最终入口：

```bash
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
  http://211.143.159.218:8223/ssh/

curl -i http://211.143.159.218:8223/sshwifty/socket/verify
```

- 页面预期返回 200。
- 配置了入口密码时，首次无认证访问 verify 接口返回 403 并包含 `X-Key` 认证挑战属于正常情况。
- `404 Route Not Found` 表示应先检查网关路径、Host、方法和状态。
- `Unexpected backend query status: 404` 应重点检查 `/sshwifty/socket/verify` 是否被路由覆盖，以及是否被错误重写。

## 9. HTTP 登录限制

公网 HTTP 下出现以下错误：

```text
Unable to authenticate:
TypeError: Cannot read properties of undefined (reading 'importKey')
```

原因是浏览器在非安全上下文中限制 Web Crypto API。当前原版 Sshwifty 没有通过启动参数关闭这项依赖的方案；清空网页入口密码也无法根治。

临时浏览器测试例外不适合作为所有访问电脑的统一配置。长期使用应提供受浏览器信任的 HTTPS。

## 10. 保留公网映射，将内网 9080 改为 HTTPS

### 前提和影响

- 已有映射必须是 TCP/NAT 转发；如果公网端是 HTTP 反向代理，仅修改内网 HTTPS 不能改变公网入口协议。
- 现有公网映射保持 `211.143.159.218:8223 → 10.157.64.3:9080`。
- 修改后，通过这个端口访问的其他应用和 UI 都要改用 HTTPS，不能只改变 `/ssh/`。
- 先准备证书并在 APISIX 添加 SSL 记录，再切换监听，保留服务器控制台及配置备份。

### 证书选择

| 方案 | 访问端要求 |
|---|---|
| mkcert 私有 CA 签发的公网 IP 证书 | 每台访问设备必须信任该 CA |
| 公开 CA 签发的公网 IP 证书 | 现代且信任对应 CA 的浏览器通常无需额外安装证书 |

只有 IP 也可以使用 HTTPS，但证书必须包含实际访问的公网 IP。仅给内网 IP 签发证书不符合公网访问地址。

Let’s Encrypt 的免费 IP 证书有效期约六天，需要自动续期和 IP 控制权验证。只有既有公网 8223、无法提供公网 80 或 443 的相应验证入口时，不能套用 Certbot HTTP-01 申请流程。

### 使用 mkcert 的固定设备方案

在用于访问页面的 Mac 上执行：

```bash
brew install mkcert
mkcert -install

mkdir -p ~/sshwifty-tls
cd ~/sshwifty-tls
mkcert -cert-file sshwifty.pem -key-file sshwifty-key.pem 211.143.159.218
```

mkcert 默认服务器证书有效期约两年三个月，根 CA 约十年；服务器证书到期后仍需替换。

如 `mkcert -install` 要求 Sudo password，通常是当前有管理员权限的 Mac 账号登录密码。没有管理员权限时可取消该步骤，直接运行生成命令：生成证书本身不要求安装系统信任。

可以尝试只为当前用户安装 SSL 信任：

```bash
security add-trusted-cert \
  -r trustRoot \
  -p ssl \
  -k "$HOME/Library/Keychains/login.keychain-db" \
  "$(mkcert -CAROOT)/rootCA.pem"
```

这仍可能要求用户授权，或被设备管理策略禁止。没有成功安装信任，不等于浏览器已经信任证书。此情况需管理员协助或改用公开 CA 证书。

在 APISIX UI 的 SSL/证书管理中添加：

| 配置项 | 内容 |
|---|---|
| SNI | 211.143.159.218 |
| cert | sshwifty.pem 完整内容 |
| key | sshwifty-key.pem 完整内容 |
| 状态 | 启用 |

通过可信管理通道配置服务器私钥。不要把证书私钥或 APISIX 管理密钥写入文档。

### 修改 APISIX 持久配置

Docker 部署先找出实际容器名称及挂载：

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}'
docker inspect APISIX容器名 \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

找到挂载到 `/usr/local/apisix/conf/config.yaml` 的宿主机配置文件，先备份，再编辑。原生部署则编辑该实例实际使用的 `conf/config.yaml`。

将下面片段合并到已有 `apisix:` 配置块。保留其他设置，不要覆盖整个文件；9081 需确认空闲，其他已有监听按实际需要保留：

```yaml
apisix:
  node_listen:
    - 9081
  ssl:
    enable: true
    listen:
      - port: 9080
    fallback_sni: "211.143.159.218"
```

含义是将 HTTP 从 9080 移到 9081，将 HTTPS 放在 9080。正确参数名称为 `fallback_sni`，不是 `default_sni`。HTTP 和 HTTPS 不能同时占用同一个地址的 9080。

Docker 原有宿主机 9080 → 容器 9080 的发布保持不变，不需要新增端口发布。不要只在临时容器文件中修改配置。

重新加载：

```bash
# Docker 部署：替换为实际容器名
docker exec APISIX容器名 apisix reload
```

原生部署：

```bash
sudo apisix reload
```

### 验证 HTTPS

先在内网测试 TLS 连通性：

```bash
curl -vk https://10.157.64.3:9080/ssh/
```

这里使用 `-k` 仅为测试内网地址的 TLS 连通性，不能证明公网证书受信任，因为证书签发给了公网 IP。

再在已信任 CA 的电脑上执行，不加 `-k`：

```bash
curl -v https://211.143.159.218:8223/ssh/
```

最终入口：`https://211.143.159.218:8223/ssh/`。

如内网成功而公网失败，应核实映射类型、转发策略及协议限制。若切换影响业务，则恢复备份的 APISIX 配置并重新加载，临时恢复原 HTTP 入口。

其他电脑需安装的是 `mkcert -CAROOT` 目录内的 `rootCA.pem`。严禁分发 `rootCA-key.pem`。不要把服务器私钥当作访问端安装的根证书。

## 11. 登录 SSH 与最终验收

在浏览器中先输入 Sshwifty 网页入口密码，再新增 SSH 连接并填写目标地址、端口、用户名和 SSH 凭据。

- 连接其他服务器：填写该服务器从 Sshwifty 容器可达的 IP 和 SSH 端口。
- 连接部署 Sshwifty 的宿主机：可填写 `host.docker.internal:22`，前提是按上述启动参数添加映射，且宿主机 SSH 监听对应接口、访问策略允许容器连接。
- 容器中的 `127.0.0.1` 指向容器自身，不是宿主机。
- 首次连接核对目标服务器的 SSH 主机密钥指纹。

最终验收应全部满足：

1. 公网 HTTPS 页面没有证书警告。
2. 页面脚本正常加载，入口密码认证成功。
3. WebSocket 连接建立，无接口 404。
4. SSH 登录成功，可以执行命令并持续显示输出。
5. 容器重启后服务仍可用；若使用公开短期证书，自动续期和证书同步均验证成功。

## 12. 常见故障速查

| 现象 | 优先检查 |
|---|---|
| `curl -I /` 返回 501 | 改用 GET 请求验证，不据此认定容器故障 |
| 找不到默认配置文件 | 后续是否从环境变量加载并出现 Serving |
| `404 Route Not Found` | URI 是否有反引号、路由状态、Host 和方法限制 |
| 页面加载但后端查询 404 | 同一条路由是否包含 `/sshwifty/*`；是否错误保留固定重写 uri |
| 网页脚本请求得到 HTML | 资源被重写到首页，检查 proxy-rewrite |
| `importKey` undefined | 是否仍使用公网 HTTP，或 HTTPS 证书未被正常信任 |
| verify 首次返回 403 | 若带认证挑战且设置了入口密码，这是正常认证流程 |
| 网关 502/504 | APISIX 到 10.157.127.3:8182 的网络、端口和服务状态 |
| 页面正常但终端断开 | WebSocket 开关、代理超时和服务端到目标 SSH 的连通性 |
| 时间差超限提示 | 浏览器电脑与服务器的时间同步；重载页面后复测 |

## 官方参考

- [Sshwifty 安装、环境变量与 FAQ](https://github.com/nirui/sshwifty)
- [Sshwifty Docker 镜像](https://hub.docker.com/r/niruix/sshwifty)
- [毫秒镜像加速说明](https://1ms.run/guide)
- [APISIX 路由、WebSocket 和 SSL API](https://apisix.apache.org/docs/apisix/admin-api/)
- [APISIX proxy-rewrite](https://apisix.apache.org/docs/apisix/plugins/proxy-rewrite/)
- [APISIX HTTPS 监听与 fallback_sni](https://apisix.apache.org/docs/apisix/FAQ/)
- [mkcert 安装与信任管理](https://github.com/FiloSottile/mkcert)
- [Let’s Encrypt IP 证书与 Certbot 支持](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)
