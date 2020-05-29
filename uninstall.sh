#!/usr/bin/env sh

if [ "$(id -u)" -eq 0 ]
then
  systemctl stop rdma
  systemctl disable rdma
  rm /etc/systemd/system/rdma.service
  rm /usr/local/sbin/rdma_py3.py
  rm /etc/rdma.ini
#  rm /etc/cron.hourly/rtsa
  rm /etc/logrotate.d/rdma

  echo "rdma is uninstalled, removing the uninstaller in progress"
  rm /usr/local/bin/uninstall-rdma.sh
  echo "##### Reboot isn't needed #####"
else
  echo "You need to be ROOT (sudo can be used)"
fi

