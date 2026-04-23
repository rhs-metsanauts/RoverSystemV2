sudo cp ~/Downloads/wifi_connect.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/wifi_connect.sh
sudo systemctl restart wifi-autoconnect.service
sudo systemctl status wifi-autoconnect.service