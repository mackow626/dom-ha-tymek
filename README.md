# Nowy Home Assistant + most do starego HA

Nowy HA w Dockerze, który pokazuje dane ze starego HA dewelopera (0.118.5) i pozwala sterować
światłami, gniazdkami, roletami, bramą i temperaturą na termostatach. Na starym HA niczego się nie instaluje ani nie zmienia.

## Jak to działa

| Kontener    | Rola |
|-------------|------|
| `homeassistant` | nowy Home Assistant, port `8123` (sieć hosta) |
| `ha-bridge` | most: czyta stany starego HA i zapisuje je w nowym HA jako `sensor.dom_*` / `binary_sensor.dom_*` |
| `ha-mqtt`   | broker MQTT nasłuchujący tylko lokalnie na serwerze (127.0.0.1); przez niego nowy HA dostaje kontrolki: `climate.*`, `switch.*`, `cover.*` (te same nazwy co w starym HA) |

Zapis do starego HA jest ograniczony listami w `.env` (nazwy po przecinku, `*` = wszystkie, puste = brak sterowania):
- `CLIMATE_CONTROL`: tylko `climate.set_temperature`, w zakresie `MIN_SETPOINT`–`MAX_SETPOINT`;
  zmiana trybu (włącz/wyłącz ogrzewanie) jest zablokowana,
- `SWITCH_CONTROL`: `switch.turn_on` / `turn_off`,
- `COVER_CONTROL`: otwórz, zamknij, stop i ustaw pozycję rolety lub bramy,
- każde polecenie trafia do logu ze słowem `ZAPIS`,
- gdy most nie ma połączenia ze starym HA, kontrolki w nowym HA są „niedostępne”.

Tokeny są tylko w pliku `.env` na serwerze. Nie ma ich w repozytorium.

## Co jest w repozytorium, a czego nie ma

W repozytorium jest kod mostu, `docker-compose.yml`, konfiguracja Mosquitto i pliki YAML nowego HA
(`config/configuration.yaml`, `config/packages/` i inne) oraz dashboard „DOM”
(`config/.storage/lovelace*`, generowany przez `dashboard/dom.py`). Po zmianie dashboardu w UI zapisz go w gicie:
`git add config/.storage/lovelace* && git commit -m "Dashboard"`.

Poza repozytorium (w `.gitignore`) są:
- `.env` z tokenami,
- reszta `config/.storage` (użytkownicy, tokeny, integracje),
- baza danych i logi.

Te pliki przenosisz ręcznie, patrz krok 3.

---

## Uruchomienie na serwerze z Linuksem

### 1. Docker
```
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # potem wyloguj się i zaloguj ponownie
```

### 2. Kod
```
git clone <adres-repozytorium> dom-ha
cd dom-ha
```

### 3a. Przeniesienie obecnego HA z Maca (zalecane)
Zachowasz konto, dashboard „Dom”, integrację MQTT, tokeny i parowanie aplikacji w telefonie.

Na Macu, w folderze `dom-ha`:
```
docker compose stop
COPYFILE_DISABLE=1 tar --no-mac-metadata -czf ha-stan.tgz --exclude='config/home-assistant.log*' config .env
scp ha-stan.tgz UZYTKOWNIK@IP_SERWERA:~/
```

Na serwerze, w folderze `dom-ha`:
```
tar -xzf ~/ha-stan.tgz
chmod 600 .env
docker compose up -d --build
```

Gdy na serwerze wszystko działa, wyłącz stack na Macu, żeby nie działały dwa mosty naraz:
```
docker compose down
```

### 3b. Albo czysta instalacja
```
cp .env.example .env && chmod 600 .env
nano .env                          # wpisz OLD_HA_TOKEN
docker compose up -d homeassistant mosquitto
```
1. Otwórz `http://IP_SERWERA:8123` i przejdź kreator.
2. Profil → Bezpieczeństwo → Tokeny długoterminowe → utwórz token. Wpisz go do `.env` jako `NEW_HA_TOKEN`.
3. Uruchom most:
   ```
   docker compose up -d --build bridge
   ```
4. Ustawienia → Urządzenia i usługi → Dodaj integrację → **MQTT**.
   Broker: `mosquitto`, port: `1883`, użytkownik i hasło puste.
5. Dashboard „DOM” pojawi się sam, bo jest w repozytorium. Encje zaczną się wyświetlać po kroku 3 i 4.

### 4. Sprawdzenie
```
docker compose ps
docker compose logs -f bridge
```
W logach powinny się pojawić:
- „Połączono ze starym HA (wersja 0.118.5)”
- „Pobrano N encji”
- „Połączono z MQTT”
- „opublikowano 11 termostatów”

### 5. Aplikacja w telefonie
W aplikacji HA wejdź w Ustawienia → Aplikacja → serwer i ustaw adres na `http://IP_SERWERA:8123`
(albo `http://NAZWA_SERWERA.local:8123`). Telefon musi być w tej samej sieci Wi-Fi.

### Ważne w sieci domowej
- W routerze zarezerwuj stałe adresy IP (DHCP) dla starego HA (`192.168.0.19`) i dla serwera.
- Dostęp spoza domu: najprościej Tailscale na serwerze i w telefonie
  (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`).

---

## Codzienna obsługa

| Co | Polecenie |
|----|-----------|
| Aktualizacja kodu | `git pull && docker compose up -d --build` |
| Aktualizacja nowego HA | `docker compose pull homeassistant && docker compose up -d homeassistant` |
| Logi mostu | `docker compose logs -f bridge` |
| Wysłane polecenia temperatury | `docker compose logs bridge \| grep -E "ZAPIS\|potwierdził"` |
| Szybkie odświeżenie encji | `docker compose restart bridge` |
| Zatrzymanie wszystkiego | `docker compose down` |
| Kopia zapasowa | `docker compose stop && tar -czf ha-kopia-$(date +%F).tgz config .env && docker compose start` |

**Tylko odczyt, bez sterowania:** w `.env` ustaw `CLIMATE_CONTROL=` (pusto), potem
`docker compose up -d --force-recreate bridge`.

**Natychmiastowe odcięcie dostępu do starego HA:** usuń token w profilu starego HA.

## Ograniczenia
- Encje `dom_*` są tylko do odczytu. Możesz ich używać na dashboardach i w automatyzacjach
  jako wyzwalaczy i warunków.
- Po restarcie nowego HA encje wracają przy najbliższej resynchronizacji (domyślnie do 5 minut)
  albo od razu po `docker compose restart bridge`.

## Dashboard „DOM”

Zakładki: **Przegląd** (alarmy, brama, szybkie akcje, włączone światła, temperatury), **Światła**
(pokojami, parter / piętro / na zewnątrz), **Rolety**, **Ogrzewanie** (strefy i termostaty), **Historia**.

Dashboard jest generowany ze skryptu `dashboard/dom.py`. Zmieniasz pokoje i nazwy w tym pliku, potem:
```
docker compose run --rm -v ./dashboard:/app/dashboard bridge python dashboard/dom.py
```
Na Macu można też po prostu: `python3 dashboard/dom.py`.
Zmiany zrobione ręcznie w UI zostaną nadpisane przy następnym uruchomieniu skryptu.

Skrypty „Zgaś światła” i „Rolety w dół / w górę” są w `config/packages/skroty.yaml`.

## Karta termostatów (stara, ręczna)
```yaml
type: vertical-stack
cards:
  - type: entities
    title: Strefy
    entities:
      - input_number.temperatura_parter
      - input_number.temperatura_pietro
      - input_number.temperatura_dom
  - type: grid
    columns: 2
    square: false
    cards:
      - type: thermostat
        entity: climate.ogrzewanie_salon
      - type: thermostat
        entity: climate.ogrzewanie_kuchnia
      - type: thermostat
        entity: climate.ogrzewanie_p1_pokoj_1
      - type: thermostat
        entity: climate.ogrzewanie_p1_pokoj_2
```
Suwaki stref pochodzą z `config/packages/strefy.yaml`. Pozostałe pokoje dodajesz tak samo (`climate.ogrzewanie_...`).

## Sieć na serwerze

- Wszystkie kontenery działają w sieci hosta (`network_mode: host`) – wymagane przez HomeKit (mDNS).
- Mosquitto nasłuchuje tylko na `127.0.0.1:1883`; w integracji MQTT w HA broker to `127.0.0.1`.
- Klimatyzatory Gree są w osobnym VLAN-ie (VLAN 2, `192.168.3.0/24`). Serwer ma w nim drugi interfejs
  `enp1s0.2` z adresem `192.168.3.250` (bez bramy) – plik `/etc/network/interfaces.d/klimy`.
  W HA: Ustawienia → System → Sieć → Karta sieciowa: zaznaczone `enp1s0` i `enp1s0.2`.
