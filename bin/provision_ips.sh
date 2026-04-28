#!/bin/bash
#/opt/pmi/bin/provision_ips.sh

# Настройки оркестратора
NS_NAME="vlan500-jmeter"
BASE_IPS="/opt/pmi/profiles/jmeter/source_ips.csv"
ATTACK_IPS="/opt/pmi/profiles/jmeter/attack_ips.csv"
TMP_BATCH="/tmp/ip_batch_cmd.txt"

# 2. Автоопределение интерфейса в netns (исключая lo)
IFACE=$(ip netns exec "$NS_NAME" ip -o link show | awk -F': ' '$2!= "lo" {print $2}' | head -n 1)

if [ -z "$IFACE" ]; then
    echo "Error: No interface found in namespace $NS_NAME"
    exit 1
fi

echo "Provisioning interface: $IFACE in netns $NS_NAME"

# 3. Тюнинг ARP на хостовой ОС (обязательно для маски /16)
sysctl -w net.ipv4.neigh.default.gc_thresh1=32768 > /dev/null
sysctl -w net.ipv4.neigh.default.gc_thresh2=49152 > /dev/null
sysctl -w net.ipv4.neigh.default.gc_thresh3=65536 > /dev/null

# 4. Настройка rp_filter внутри netns (Loose mode для асимметричного трафика)
ip netns exec "$NS_NAME" sysctl -w net.ipv4.conf.all.rp_filter=2 > /dev/null
ip netns exec "$NS_NAME" sysctl -w net.ipv4.conf."$IFACE".rp_filter=2 > /dev/null
ip netns exec "$NS_NAME" sysctl -w net.ipv4.ip_local_port_range="1024 65535" > /dev/null
ip netns exec "$NS_NAME" sysctl -w net.ipv4.tcp_tw_reuse=1 > /dev/null
ip netns exec "$NS_NAME" sysctl -w net.ipv4.tcp_fin_timeout=15 > /dev/null

# 5. Формирование batch-файла из ОБЕИХ баз
echo "Merging Baseline and Attack IPs..."
# Читаем оба файла (ошибки игнорируем, если файла вдруг нет) и передаем в awk
cat "$BASE_IPS" "$ATTACK_IPS" 2>/dev/null | awk -v iface="$IFACE" '{print "addr add " $1 "/16 dev " iface}' > "$TMP_BATCH"

# 6. Мгновенное применение через ip -batch
echo "Applying massive IP pool..."
ip netns exec "$NS_NAME" ip -batch "$TMP_BATCH"

rm "$TMP_BATCH"
echo "Done. Total IPs in $NS_NAME: $(ip netns exec "$NS_NAME" ip addr show dev "$IFACE" | grep -c "inet ")"