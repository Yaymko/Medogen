# Развёртывание medogen на внутреннем сервере

Здесь описано, как поставить один общий экземпляр интерфейса для всей команды, чтобы
тестировщики работали в браузере и ничего не устанавливали у себя.

## Что нужно знать заранее

- Сервер — это `python medogen.py ui`. Он написан только на стандартной библиотеке Python
  (http.server), отдельный веб-сервер или база данных ему не нужны.
- **В интерфейсе нет авторизации.** Любой, кто откроет адрес, может собирать пакеты, загружать
  файлы и править стенды. Поэтому сервер ставится только во внутренней сети. Если нужен вход по
  паролю или доменной учётной записи, интерфейс закрывают обратным прокси с авторизацией
  (раздел 4).
- Общее у всех пользователей: стенды (`orgs/*.json`), справочник ГАС (`gas/gas.json`), список
  пакетов (`out/ui`). Пакет, собранный одним человеком, виден всем на вкладке «Пакеты».
- Пути к пакетам, которые показывает интерфейс, — это пути на сервере. Пользователи получают
  пакеты кнопкой «Скачать .zip».
- Нагрузка небольшая: одна сборка занимает доли секунды. Хватает 1 vCPU и 1 ГБ ОЗУ, на диске
  нужно около 100 МБ плюс место под папку `out`.

## 1. Установка

Нужны Python 3.8+ и git. lxml необязателен, но с ним пакеты проверяются по XSD.

**Linux (пример для Debian/Ubuntu/Astra):**

```
sudo apt install -y python3 python3-venv git
sudo useradd -r -m -d /opt/medogen -s /usr/sbin/nologin medogen
sudo -u medogen git clone https://github.com/Yaymko/Medogen.git /opt/medogen/app
sudo -u medogen python3 -m venv /opt/medogen/venv
sudo -u medogen /opt/medogen/venv/bin/pip install lxml
```

Репозиторий приватный, поэтому для `git clone` нужен доступ: личный токен GitHub (fine-grained,
только чтение этого репозитория) или deploy key (SSH-ключ только на чтение, задаётся в
Settings → Deploy keys репозитория). Если у сервера нет выхода в интернет, скопируйте папку
medogen вручную, например архивом из GitHub (Code → Download ZIP).

**Windows Server:**

```
git clone https://github.com/Yaymko/Medogen.git C:\medogen
cd C:\medogen
python -m pip install lxml
```

Проверка: `python medogen.py build examples` должен закончиться строкой «ошибок: 0», а
`python -m unittest discover -s tests` — строкой «OK».

## 2. Справочники

После установки загрузите общие данные (это можно сделать и потом, через интерфейс, вкладка
«Организации»):

- **Справочник ГАС.** Сохраните https://gosedo.ru/wp-content/uploads/files/addresseesActual.html
  и выполните `python medogen.py import-gas addresseesActual.html`. Файл `gas/gas.json`
  занимает около 14 МБ и в репозиторий не входит.
- **Стенды.** Положите в `orgs/` справочники организаций своих тестовых контуров. Их можно
  создать в интерфейсе или командой `python medogen.py import-orgs ... orgs/стенд.json`.

## 3. Запуск как службы

Сервер слушает адрес и порт из параметров `--host` и `--port` (по умолчанию 127.0.0.1:8765).
`--no-browser` отключает попытку открыть браузер на сервере.

**Linux, systemd** — файл `/etc/systemd/system/medogen.service`:

```
[Unit]
Description=medogen - генератор тестовых пакетов МЭДО
After=network.target

[Service]
User=medogen
WorkingDirectory=/opt/medogen/app
ExecStart=/opt/medogen/venv/bin/python medogen.py ui --host 127.0.0.1 --port 8765 --no-browser
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```
sudo systemctl daemon-reload
sudo systemctl enable --now medogen
sudo systemctl status medogen
```

Здесь сервер слушает только 127.0.0.1, а снаружи доступен через прокси (раздел 4). Если
прокси не нужен, укажите `--host 0.0.0.0` и откройте порт 8765 в межсетевом экране только для
внутренней сети.

**Windows** — проще всего через NSSM (https://nssm.cc):

```
nssm install medogen "C:\Python312\python.exe" "C:\medogen\medogen.py ui --host 0.0.0.0 --port 8765 --no-browser"
nssm set medogen AppDirectory C:\medogen
nssm start medogen
netsh advfirewall firewall add rule name="medogen" dir=in action=allow protocol=TCP localport=8765 remoteip=localsubnet
```

Без NSSM можно создать задачу в Планировщике заданий: «При запуске компьютера», от имени
служебной учётной записи, с той же командой.

### Доступ к папкам СЭД (OUT и IN)

Если тестировщики забирают пакеты из OUT и кладут ответы в IN (см. инструкцию пользователя,
раздел 5), к этим папкам обращается сама служба medogen, а не браузер пользователя.

- **Windows.** Запускайте службу от доменной учётной записи с правами на чтение и изменение
  OUT, OUTArchive и IN: `nssm set medogen ObjectName DOMAIN\svc-medogen <пароль>`. В стенде
  указывайте UNC-путь (`\\srv-sed\MEDO\OUT`), а не букву сетевого диска: подключённые диски
  службе не видны.
- **Linux.** Подключите общую папку через cifs, например в `/etc/fstab`:
  `//srv-sed/MEDO /mnt/sed cifs credentials=/etc/medogen-smb,uid=medogen,gid=medogen,iocharset=utf8 0 0`,
  и укажите в стенде `/mnt/sed/OUT`, `/mnt/sed/OUTArchive`, `/mnt/sed/IN`.
- Перенос из OUT в OUTArchive быстрый и надёжный, когда обе папки лежат на одном общем ресурсе.
- Пути задаются в стенде, а в интерфейсе нет авторизации. Закройте его прокси (раздел 4), чтобы
  посторонний не мог направить службу на другие папки.

## 4. Обратный прокси с авторизацией (рекомендуется)

nginx даёт постоянное имя (`http://medogen.corp.local`), HTTPS и вход по паролю.

```
server {
    listen 80;
    server_name medogen.corp.local;

    client_max_body_size 200m;          # загрузка входящих пакетов и справочника ГАС (16 МБ)

    auth_basic "medogen";
    auth_basic_user_file /etc/nginx/medogen.htpasswd;   # htpasswd -c /etc/nginx/medogen.htpasswd user

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_read_timeout 300s;
    }
}
```

Для входа по доменной учётной записи подойдёт тот прокси, который уже есть в организации
(nginx с модулем auth_request, IIS с Windows-аутентификацией и ARR и т. п.). medogen работает за
любым из них без изменений.

## 5. Обновление

```
cd /opt/medogen/app
sudo -u medogen git pull
sudo -u medogen /opt/medogen/venv/bin/python -m unittest discover -s tests
sudo systemctl restart medogen
```

На Windows то же самое: `git pull`, тесты, `nssm restart medogen`.

`git pull` не затрагивает пакеты (`out/`), справочник ГАС (`gas/gas.json`) и стенды, которые
вы добавили сами: эти файлы не отслеживаются git. Изменения стенда-примера
`orgs/default.json` git сохранит как локальные. Если `pull` из-за них откажется обновляться,
перенесите правки в отдельный файл стенда.

## 6. Обслуживание

- **Резервная копия:** `orgs/` и `gas/gas.json`. Папку `out/` можно не сохранять, пакеты
  пересобираются.
- **Очистка:** папка `out/ui` растёт со временем. Старые пакеты можно удалять по расписанию,
  например `find /opt/medogen/app/out/ui -maxdepth 1 -mtime +30 -exec rm -rf {} +`.
  Служебные папки `_incoming`, `_uploads`, `_scenarios` и `_manifests` чистятся так же.
- **Журнал:** `journalctl -u medogen` (Linux) или журнал NSSM (Windows).
- **Подписи КриптоПро:** `settings.sign_command` (см. README, раздел «Подписи») сейчас
  задаётся только в сценариях командной строки. Пакеты, собранные в интерфейсе, всегда
  подписываются встроенным тестовым сертификатом. Если команда задана и на сервере стоит
  КриптоПро CSP, служба должна работать от учётной записи с доступом к контейнеру ключа.
