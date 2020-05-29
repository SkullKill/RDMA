#!/usr/bin/env sh

systemctl -q is-active rdma  && { echo "ERROR: rdma service is still running. Please run \"sudo systemctl stop rdma\" to stop it."; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "You need to be ROOT (sudo can be used)"; exit 1; }

# log2ram
mkdir -p /usr/local/sbin/
mkdir -p /var/log/rdma/
install -m 644 rtsa.service /etc/systemd/system/rdma.service
install -m 755 rtsa_py3.py /usr/local/sbin/rdma_py3.py
install -m 644 rtsa.ini /etc/rdma.ini
install -m 644 uninstall.sh /usr/local/sbin/uninstall-rdma.sh
systemctl daemon-reload
systemctl enable rtsa
systemctl start rtsa

# cron
#install -m 755 rtsa.hourly /etc/cron.hourly/rtsa
install -m 644 rdma.logrotate /etc/logrotate.d/rdma

echo "#####         rdma installed         #####"
echo "##### edit /etc/rdma.ini to configure options ####"

