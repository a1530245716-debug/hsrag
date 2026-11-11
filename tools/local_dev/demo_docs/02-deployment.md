# 部署手册

## 安装包

- trade-gateway-1.4.2.zip
- order-service-1.4.2.jar
- market-service-1.4.2.jar
- ops-console-1.4.2.war

## 部署步骤

1. 检查 JDK 版本，要求 17 及以上。
2. 解压安装包到 `/opt/demo-platform`。
3. 修改 `conf/application.yml` 中的数据库和消息队列地址。
4. 执行 `bin/start.sh` 启动各服务。
5. 访问运维控制台，确认节点状态为在线。

## 回滚

保留上一版本目录，执行 `bin/stop.sh` 后切回旧目录并重新启动。
