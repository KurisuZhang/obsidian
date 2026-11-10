# Ubuntu 24.04 永久 DNS 配置（nd02）

## 目的

将 `bond0` 的 DNS 设置持久化，避免临时配置在重启、网卡重新配置或网络服务重启后丢失。

本文记录的是 nd02 的配置方案。此前提供的输出确认了现有网络结构；本文不代表已经在服务器上执行并完成验证。

## 适用环境

| 项目 | 配置 |
|---|---|
| 主机 | nd02 |
| 系统 | Ubuntu 24.04 |
| 配置管理 | Netplan |
| 默认出口 | bond0 |
| bond0 地址 | 10.157.64.2/24 |
| 默认网关 | 10.157.64.254 |
| bond0 成员 | eno1np0、eno2np1 |
| bond 模式 | 802.3ad（LACP） |
| DNS 服务器 | 211.138.151.161、211.138.156.66 |
| DNS 路由域 | `~.` |

bond1 使用 `10.157.127.2/24`，bond2 使用 `10.157.80.2/24`。本次不修改这两个接口，也不改变任何接口的 IP、网关和 bond 参数。

bond0 当前为静态地址，未启用 DHCP，因此不需要添加 `dhcp4-overrides` 或 `dhcp6-overrides`。

## 临时配置为何会丢失

以下命令修改的是 systemd-resolved 的运行时状态，不会写回 Netplan 配置：

```bash
sudo resolvectl dns bond0 211.138.151.161 211.138.156.66
sudo resolvectl domain bond0 '~.'
sudo resolvectl flush-caches
```

其中 `flush-caches` 只清理 DNS 缓存，不需要持久化。`~.` 是仅用于 DNS 路由的根域，使没有更具体 DNS 路由匹配的查询优先使用该链路的 DNS；它不是普通的域名搜索后缀。

## 配置步骤

以下命令在 nd02 上执行。重新应用网络配置可能短暂影响连接，建议保留服务器控制台入口。

### 1. 确认并备份现有配置

```bash
hostname
sudo netplan get
ls -l /etc/netplan/
```

确认仍与本文环境一致，然后备份：

```bash
backup_dir=$(sudo mktemp -d /root/netplan-dns-backup.XXXXXX)
sudo cp -a /etc/netplan/. "$backup_dir/"
printf '备份目录：%s\n' "$backup_dir"
```

记下打印的备份目录，以便恢复。

### 2. 新增 DNS 补充文件

目标文件：`/etc/netplan/99-bond0-dns.yaml`。

如果该文件已经存在，先查看其内容并合并修改，不要直接覆盖其他用途的配置。首次创建时执行：

```bash
sudo tee /etc/netplan/99-bond0-dns.yaml >/dev/null <<'EOF'
network:
  version: 2
  bonds:
    bond0:
      nameservers:
        addresses:
          - 211.138.151.161
          - 211.138.156.66
        search:
          - "~."
EOF

sudo chmod 600 /etc/netplan/99-bond0-dns.yaml
```

Netplan 会将该文件与原有配置合并，因此这里不重复定义成员网卡、IP、路由和 bond 参数。不要删除原有 YAML 文件，也不要用本文件替换完整网络配置。

### 3. 检查配置后应用

```bash
sudo netplan generate
sudo netplan get
```

确认生成过程没有错误；合并后的 bond0 包含上述 nameservers，原有地址、默认路由、成员网卡及 bond 参数仍然存在。如果发现其他 DNS 地址或路由域，检查其他 YAML 文件是否也定义了 nameservers，避免多处配置合并产生非预期结果。

检查通过后执行：

```bash
sudo netplan apply
sudo resolvectl flush-caches
```

无需修改 `/etc/resolv.conf`，也无需编写开机重复执行 resolvectl 的任务。

## 验证与验收

### 立即验证

```bash
resolvectl status bond0
resolvectl query example.com
ip -4 route show default
```

预期结果：

- bond0 的 DNS Servers 包含 `211.138.151.161` 和 `211.138.156.66`。
- DNS Domain 包含 `~.`。
- 域名查询成功，并在正常情况下经 bond0 解析。
- 默认路由仍通过 `10.157.64.254`，出口仍为 bond0。

`Current DNS Server` 只显示当前使用的一台服务器属于正常情况，不要求两个地址同时显示为当前服务器。

### 重启后验证

在允许重启的维护时间重启服务器；不要仅为验证 DNS 直接重启有业务负载的机器。重启后再次执行：

```bash
resolvectl status bond0
resolvectl query example.com
```

配置仍存在且解析成功，才算完成持久化验收。

## 回滚

如果本次是首次新增 `/etc/netplan/99-bond0-dns.yaml`，且之后没有在该文件中添加其他配置，可以移除这个补充文件：

```bash
sudo rm /etc/netplan/99-bond0-dns.yaml
sudo netplan generate
sudo netplan apply
sudo resolvectl flush-caches
```

如果本次修改的是已有文件，则从前面记录的备份目录恢复该文件，再执行生成和应用命令。回滚时只恢复本次改动，避免覆盖之后新增的其他网络配置。

本机原始配置没有持久 DNS；回滚后可能无法解析域名。如需临时恢复解析，可执行本文开头的 resolvectl 命令。

## 常见问题

| 现象 | 检查方式 |
|---|---|
| `netplan generate` 报 YAML 错误 | 使用空格缩进，检查层级；不要使用 Tab。修正前不要 apply。 |
| 配置文件权限警告 | 执行 `sudo chmod 600 /etc/netplan/99-bond0-dns.yaml`。 |
| 配置中出现非预期 DNS | 查看 `sudo netplan get` 和其他 YAML 文件；Netplan 列表可能合并，不能假设后一个文件会完全替换前面的列表。 |
| DNS 已显示，但查询失败 | 检查到两个 DNS 地址的网络、路由及 UDP/TCP 53 访问策略；DNS 设置不会自动解决网络不通。 |
| `resolvectl` 查询成功，普通程序仍无法解析 | 检查 `ls -l /etc/resolv.conf` 以及 `cat /etc/resolv.conf`，确认解析器接入方式；不要直接覆盖该文件。 |
| 重启后设置消失 | 检查补充文件是否仍在，以及 cloud-init、运维脚本或配置管理系统是否重写了网络配置。 |

## 官方参考

- [Netplan YAML 配置：nameservers 与 DHCP overrides](https://netplan.readthedocs.io/en/stable/netplan-yaml/)
- [resolvectl：运行时 DNS 配置命令](https://www.freedesktop.org/software/systemd/man/latest/resolvectl.html)

注意：DNS 配置与 Sshwifty 的 HTTP/HTTPS 登录限制是两个独立问题，修改 DNS 不会让 HTTP 页面自动获得安全上下文。
