#!/bin/bash
#/opt/pmi/bin/init_jmeter_ns.sh

NS_NAME="vlan500-jmeter"
IFACE="ens29f1np1"

case "$1" in
    start)
        echo "Создаём namespace $NS_NAME..."
        ip netns add "$NS_NAME"
        ip netns exec "$NS_NAME" ip link set lo up

        echo "Перемещаем физический интерфейс $IFACE в $NS_NAME..."
        ip link set "$IFACE" netns "$NS_NAME"

        echo "Настраиваем адрес и маршруты..."
        ip netns exec "$NS_NAME" ip addr add 10.0.50.4/29 dev "$IFACE"
        ip netns exec "$NS_NAME" ip link set "$IFACE" up
        ip netns exec "$NS_NAME" ip route add default via 10.0.50.2 dev "$IFACE"

        echo "Запускаем provision_ips.sh для генерации пула..."
        /opt/pmi/bin/provision_ips.sh
        
        echo "JMeter Network Namespace ГОТОВ."
        ;;
    stop)
        echo "Возвращаем интерфейс $IFACE обратно на хост..."
        ip netns exec "$NS_NAME" ip link set "$IFACE" netns 1 2>/dev/null || true
        
        echo "Удаляем namespace $NS_NAME..."
        ip netns del "$NS_NAME" 2>/dev/null || true
        ;;
    *)
        echo "Usage: $0 {start|stop}"
        exit 1
        ;;
esac
