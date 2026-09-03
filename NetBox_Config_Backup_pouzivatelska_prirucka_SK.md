# NetBox Config Backup – používateľská príručka

Táto príručka opisuje bežnú prevádzku pluginu **NetBox Config Backup 0.7.x**
v NetBoxe 4.6. Je určená operátorom aj správcom, ktorí potrebujú bezpečne
zálohovať konfigurácie sieťových zariadení, kontrolovať výsledky a udržiavať
históriu. Aktualizované **3. septembra 2026** podľa aktuálneho zdrojového kódu.
Pri inštalácii konkrétneho vydania používaj dokumentáciu z rovnakého Git tagu;
zmeny v aktuálnej vývojovej vetve nemusia byť v staršom vydaní dostupné.

Štandardné nasadenie používa lokálne úložisko pluginu ako primárnu kópiu a
voliteľné FTP, NFS alebo SMB3 úložisko ako druhú kópiu. Inštaláciu a serverové nastavenia
opisuje samostatný dokument [docs/INSTALLATION.md](docs/INSTALLATION.md).

Názvy tlačidiel a sekcií uvádzame prevažne v angličtine, ktorá je predvoleným
jazykom pluginu. Jazyk možno zmeniť v **Settings → Plugin language**.

## 1. Čo plugin robí

Pri každom pokuse o zálohu plugin:

1. vytvorí záznam **BackupRun**,
2. určí adresu, driver, connection profile a credential profile,
3. pripojí sa k zariadeniu,
4. načíta konfiguráciu alebo natívny zálohovací súbor,
5. skontroluje, že získané dáta zodpovedajú očakávanému formátu,
6. vypočíta SHA-256 hash,
7. pri potrebe vytvorí **ConfigRevision** a uloží jej artifacty,
8. zaradí kopírovanie revision na povolené vzdialené storages so zapnutým
   automatickým kopírovaním.

Bežné CLI drivery spúšťajú iba zobrazovací alebo exportný príkaz. Natívne
drivery pre niektoré zariadenia musia po výslovnom potvrdení vytvoriť
zálohovací súbor na zariadení. Plugin nikdy neimportuje konfiguráciu, neaktivuje
zmeny, nereštartuje zariadenie a nevykonáva automatický restore.

### Dôležité pojmy

| Pojem | Význam |
| --- | --- |
| Backup device / target | NetBox zariadenie zaradené do zálohovania. |
| BackupRun | Jeden pokus o zálohu vrátane stavu a prípadnej chyby. |
| ConfigRevision | Uložená verzia konfigurácie. |
| Artifact | Konkrétny súbor revision, napríklad textová konfigurácia alebo natívny ZIP/TGZ. |
| Driver | Kód, ktorý pozná bezpečný spôsob získania konfigurácie daného typu zariadenia. |
| Platform mapping | Priradenie NetBox platformy k driveru a zdieľaným profilom. |
| Connection profile | Zdieľaný protokol, výber adresy, port a timeouty. |
| Credential profile | Zdieľané prihlasovacie údaje alebo odkaz na environment secret. |
| Storage / úložisko | Miesto, kde plugin uchováva lokálnu alebo vzdialenú kópiu zálohy. |
| Local storage | Systémové primárne úložisko z `storage_root`; je vždy povolené a nedá sa zmazať. |
| Vzdialené storage | Samostatné sekundárne úložisko dokončených revisions: interné FTP alebo vopred pripojené NFS/SMB3. |
| NFS/SMB3 storage | Sieťový disk pripojený operačným systémom alebo Dockerom; plugin dostane iba cestu k mountu. SMB1 sa nepodporuje. |
| Lokálny retenčný profil | Pravidlá uchovávania lokálnych revisions, artifactov a BackupRun záznamov; môže byť na zariadení, backup policy alebo Local storage. |
| Remote retention profile | Pravidlá uchovávania vzdialených kópií revisions; vyhodnocujú sa samostatne pre každé zariadenie a každé FTP, NFS alebo SMB3 storage. |

## 2. Rýchly štart

Predpokladom je nainštalovaný plugin, spustený bežný NetBox worker aj dedikovaný
backup worker a nastavený master key.

1. Otvor **Config Backup → Settings**. Pod kartami profilov rozbaľ **Device
   drivers**, ponechaj potrebné drivery zaškrtnuté a klikni na **Save drivers**.
   Predvolene sú povolené všetky dostupné drivery.
2. V **Device defaults → Credential profiles** vytvor credential profile a v
   **Connection profiles** priprav profil spojenia.
3. V **Device defaults → Platform mappings** priraď platformu k driveru a profilom.
4. V **Schedules and retention** priprav lokálny a podľa potreby vzdialený
   retenčný profil. V **Backup policies** nastav požadovaný plán záloh.
5. Otvor **Config Backup → Devices → Add**. Vyber zariadenie; profily môže
   prevziať z mappingu. Prípadné výnimky nastav rovnakými poľami ako pri editácii.
   Na prvý manuálny test môže zostať **Policy override** prázdne; automatický
   harmonogram sa aktivuje až priradením povolenej backup policy.
6. Klikni na **Save** a na detaile backup zariadenia spusti **Test connection**.
7. Pri SSH profile skontroluj výsledok podľa zvoleného režimu identity. V
   manuálnom režime porovnaj fingerprint a schváľ host key; TOFU prijme iba
   úplne prvý kľúč automaticky.
8. Po úspešnom teste otvor detail zariadenia a klikni na **Run backup**.
9. Skontroluj výsledok v **Runs** a uložený obsah v **Revisions**.
10. Ak používaš vzdialené úložisko, spusti **Test storage**, následne **Copy
    existing revisions** a **Check stored copies**. Úspešný testovací súbor
    sám nepotvrdzuje prenos skutočnej revision.
11. Po overení priraď zariadeniu povolenú backup policy cez **Policy override**
    a skontroluj **Next run**. Automatickú retenciu zapni až po kontrole preview.

Priradenie alebo zmena retenčného profilu, aj cez backup policy, vyžaduje
oprávnenia na správu retencie a príslušné mazanie. Úvodné nastavenie preto rob
účtom zo skupiny **Config Backup Administrators**. Operátor môže existujúce ciele
testovať, spúšťať a preplánovať, ale nesmie nepriamo povoliť budúce mazanie.

Prázdna retenčná voľba na zariadení neznamená automaticky „navždy“. Lokálna
retencia najprv použije backup policy a potom profil Local storage. Vzdialená
retencia sa vyhodnocuje samostatne pre každé vzdialené storage a použije jeho profil.
Povinne vynútená politika storage má vždy prednosť pred výnimkou zariadenia.
Bez efektívnej politiky sa príslušná história uchováva bez časového limitu.

Zariadenie, ktoré už má backup target, sa vo formulári **Devices → Add** znova
nezobrazí.

### Hromadná úprava zariadení

V zozname **Devices** označ viac zariadení a vyber **Edit**. Spoločný formulár
upraví stav zapnutia, backup policy, lokálny alebo vzdialený retenčný profil,
credential/connection/receiver profile a driver override. Pre plán záloh sa po
uložení automaticky prepočíta **Next run**. JSON **Driver options override** sa
hromadne nemení, aby sa omylom nepreniesla voľba špecifická pre jeden model na
iné zariadenia. Pri voliteľnom profile prázdna voľba znamená „nechať aktuálnu
hodnotu“; jeho odstránenie treba potvrdiť prepínačom na vymazanie pri danom poli.

### Jazyk pluginu

Správca nastaví predvolený jazyk v **Config Backup → Settings → Plugin language**
na spodku stránky. Vyber **English** alebo **Slovenčina** a klikni na **Save
language**. Predvolená je angličtina; reštart nie je potrebný.
Voľba platí iba pre stránky Config Backup a nemení jazyk ostatných častí
NetBoxu. Uloženie jazyka neuloží rozpracované zmeny ostatných formulárov Settings.

Každý používateľ si môže jazyk dočasne prepnúť aj priamo v **Config Backup →
Help**. Táto osobná voľba sa uloží iba do jeho prihlásenej relácie a má prednosť
pred globálnym nastavením. Používateľ ju prepne späť rovnakými tlačidlami v
Help; po skončení relácie sa opäť použije globálny jazyk.

### Overovanie identity SSH zariadenia

Connection profile ponúka tri režimy:

- **Vyžadovať manuálne schválenie** – prvý fingerprint musí schváliť administrátor;
  ide o najbezpečnejšiu predvolenú voľbu.
- **Automaticky dôverovať prvému kľúču** – prvý kľúč zistený pre dané zariadenie,
  adresu a port sa automaticky označí ako dôveryhodný. Každá neskoršia zmena
  sa zablokuje a musí sa schváliť manuálne.
- **Neoverovať SSH identitu** – plugin identitu SSH servera nekontroluje.
  Používajte iba ako vedomú výnimku v dôveryhodnej manažmentovej sieti.

Pri manuálnom schválení náhradného kľúča sa všetky staršie dôveryhodné kľúče
rovnakého endpointu označia ako odmietnuté. Z databázy sa automaticky nemažú,
pretože slúžia ako auditná história, ale pri spojení sa už neakceptujú.
Samotné vypnutie overovania existujúce záznamy nemaže ani nemení: profil ich
počas vypnutia iba nepoužíva. Plugin zároveň počas vypnutia nedokáže rozpoznať
zmenu identity zariadenia.

## 3. Navigácia pluginu

### Overview

Domovská stránka zobrazuje:

- aktuálne počty **Healthy**, **Stale**, **Failed** a ďalších stavov,
- zaseknuté runs,
- počet runs, revisions a zlyhaní vo vybranom období,
- stav FTP úložiska,
- posledné zlyhania s dôvodom a odkazom na run alebo zariadenie,
- najnovšie runs a revisions.

Historické údaje možno zobraziť za **24 hodín**, **7 dní**, **30 dní**,
**90 dní**, **All time** alebo vlastný rozsah dátumov. Karty aktuálneho zdravia
a zaseknutých runs vždy zobrazujú živý stav bez ohľadu na zvolené obdobie.

### Devices

Zoznam zariadení zaradených do zálohovania. Obsahuje stav, driver, posledný
úspech, poslednú zmenu, počet po sebe idúcich zlyhaní a poslednú revision.
Odtiaľ možno spustiť test, manuálnu zálohu, editáciu alebo hromadné odstránenie.

Na bežnom detaile **NetBox Device → Backup** je aj záložka so zálohami tohto
zariadenia: posledné revisions, náhľad, sťahovanie a história behov. Nie je
potrebné zakaždým hľadať zariadenie v samostatnom zozname pluginu.

### Storages

Zobrazuje systémové **Local storage** a administrátorom vytvorené FTP, NFS a SMB3 storages.
Local storage reprezentuje primárny adresár pluginu, je vždy povolené a nedá sa
zmazať, vypnúť ani zmeniť na vzdialený typ. Pri každom storage možno nastaviť retenčný
fallback alebo politikou storage povinne prekryť retenčné nastavenie zariadenia.

### Runs

Každý pokus o zálohu má vlastný záznam. Connection test má samostatnú výsledkovú
stránku a technický NetBox Job. Zoznam runs možno filtrovať podľa
zariadenia, lokality, zdroja, stavu, chyby, zaseknutia a obdobia.

### Revisions

Uložené verzie konfigurácií. Oprávnený používateľ môže zobraziť náhľad,
porovnať verzie, stiahnuť artifact a pripraviť overený balík z FTP kópie.

### Settings

Profily zostávajú viditeľné hore. Menej často používané nastavenia sú pod nimi
v rozbaľovacích sekciách:

| Sekcia | Obsah |
| --- | --- |
| **Device defaults** | Platform mappings, Connection profiles a Credential profiles. |
| **Schedules and retention** | Backup policies, Local retention profiles a Remote retention profiles. |
| **Device drivers** | Zaškrtávanie dostupných driverov; aj pri zbalení vidíš počet povolených. |
| **Automation** | Local cleanup, remote cleanup a NetBox alerts; pri zbalení vidíš ich stav. |
| **Security and downloads** | Protected ZIP downloads, SSH host keys a Device upload receivers. |
| **Plugin language** | Predvolený jazyk a tlačidlo Save language, na spodku stránky. |

**Device drivers**, **Automation** a **Security and downloads** otvor kliknutím
na ich nadpis. Každý formulár má vlastné uloženie: **Save drivers**, **Save
cleanup settings**, **Save alert settings**, **Save download protection** alebo
**Save language**. Ulož vždy konkrétnu upravenú časť. Pri chybe sa príslušná
sekcia otvorí a zobrazí dôvod, prečo sa zmena neuložila.

Tieto UI voľby platia bez reštartu. Inštalácia nového kódu pluginu je odlišná:
vyžaduje aktualizáciu procesov a prípadné migrácie podľa kapitoly 25. Používateľ
bez príslušných oprávnení na zmenu vidí iba stav, nie aktívne ovládacie prvky.

### Výber používaných driverov

1. V **Settings** rozbaľ **Device drivers** pod kartami profilov.
2. Zaškrtni drivery, ktoré chceš používať, a klikni na **Save drivers**.
3. Pri pridávaní zariadenia alebo platform mappingu sa ponúknu iba povolené
   drivery. Nastavenie balíky neodinštaluje.

Voľbu mení správca s oprávnením `change_operationalsettings`. Po novej
inštalácii aj po migrácii sú všetky dostupné drivery povolené. Driver označený
**In use** (**Používané**) sa nedá vypnúť, kým ho používa výnimka zariadenia
alebo platform mapping. Počítajú sa aj vypnuté zariadenia a mappingy; najprv ich
preraď. Staršie skryté SIAE drivery sa spravujú spoločne voľbou **SIAE SM-OS**.

Vypnutie drivera nevymaže revisions ani nezablokuje náhľad a sťahovanie starej
histórie. Nový zber cez vypnutý driver sa odmietne s `DRIVER_DISABLED`. Novo
nainštalované externé drivery sú predvolene povolené; ak sa vypnutý externý
balík dočasne odinštaluje a neskôr vráti, jeho uložené vypnutie zostáva zachované.

### Help

Read-only stránka **Config Backup → Help** vysvetľuje odporúčané poradie
nastavenia, tok zálohy, rozdiel medzi lokálnym a vzdialeným úložiskom, poradie retenčných
pravidiel a prvé kontroly pri bežných error kódoch. Help nezobrazuje heslá ani
aktuálne nasadené secret hodnoty a je dostupný aj skupine Readers.

## 4. Príprava NetBox zariadenia

Plugin potrebuje použiteľnú management adresu. Connection profile určuje jej
poradie:

- **Najprv samostatná management IP (OOB)** – najskôr pole OOB IP zariadenia
  v NetBoxe, potom primárna IP,
- **Primary IPv4 first** – uprednostní primárnu IPv4,
- **Primary IPv6 first** – uprednostní primárnu IPv6.

OOB znamená *out-of-band management*: samostatná management adresa alebo sieť,
ktorá nemusí používať produkčnú dátovú cestu. Zvolená adresa musí byť dostupná
z NetBox/backup worker kontajnera, nie iba z počítača používateľa.

Odporúčania:

- nastav na zariadení správnu NetBox platformu,
- eviduj OOB alebo primárnu IP,
- použi samostatný účet určený na zálohovanie,
- prideľ mu iba práva potrebné na čítanie alebo vytvorenie natívnej zálohy,
- over dostupnosť portu z backup workera.

## 5. Credential profiles a master key

### Encrypted database

Najjednoduchšia možnosť pre interné nasadenie. Používateľské meno a heslo sa
zadá v UI. Heslo je zašifrované pomocou AES-256-GCM a po uložení sa už
nezobrazuje. Pri editácii nechaj heslo prázdne, ak ho nechceš meniť.

### Environment variables

Credential profile môže namiesto uloženého hesla odkazovať na secret v
prostredí procesu, napríklad `env://ROUTER_1`. Hodnota musí byť dostupná vo web
procese aj vo všetkých workeroch, ktoré zálohu spracujú.

### Kde je master key

Master key nevytvára plugin automaticky a neukladá sa do PostgreSQL. Vytvorí ho
správca pri inštalácii a odovzdá ho web procesu a workerom cez chránené
environment/container secrets:

```text
NETBOX_CONFIG_BACKUP_MASTER_KEY=<unikátny 256-bitový kľúč>
NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION=1
NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS={}
```

Kľúč musí byť zálohovaný oddelene od PostgreSQL. Bez neho nemožno dešifrovať
uložené credentials. Postup rotácie je v
[docs/MASTER_KEY_ROTATION.md](docs/MASTER_KEY_ROTATION.md).

## 6. Connection profiles

Connection profile sa zdieľa medzi zariadeniami s rovnakým spôsobom prístupu.
Obsahuje:

- protokol **Automatic**, **SSH** alebo **Telnet**,
- prioritu samostatnej management IP (OOB) alebo primárnej adresy,
- port,
- connect timeout,
- command timeout,
- keepalive,
- režim overovania SSH identity; dôveryhodné kľúče spravuje plugin automaticky.

**Automatic** používa rozhodnutie drivera a portu; štandardne SSH na porte 22
a Telnet na porte 23. Pri Telnete sa host-key kontrola nepoužíva.

Telnet neposkytuje šifrovanie mena, hesla ani konfigurácie. Použi ho iba tam,
kde ho zariadenie nevyhnutne vyžaduje, v izolovanej management sieti alebo cez
chránenú VPN.

## 7. Platform mappings

Platform mapping automaticky priraďuje NetBox platforme:

- backup driver,
- connection profile,
- credential profile,
- podľa potreby device upload receiver,
- obmedzené driver options.

Vďaka mappingu sa pri každom zariadení znova nevyberá výrobca, port ani login.
Ak zariadenie nemá vhodný mapping, vo formulári **Devices → Add** možno driver
a profily vybrať ručne cez príslušné polia **override**. V ponuke sú iba drivery
povolené v **Settings → Device drivers**.

Nový výrobca sa dá doplniť aj externým driver balíkom cez Python entry point.
Samotné pridanie výrobcu alebo platformy do NetBoxu však nevytvorí bezpečný
driver automaticky; driver musí poznať príkaz, formát a validáciu výstupu.

## 8. Pridanie zariadenia

Otvor **Config Backup → Devices → Add**. Vytvorenie a editácia používajú
rovnaký formulár; po uložení sa rozloženie ani názvy polí nemenia.

### Základné polia

- **Device** – zariadenie z NetBoxu, ktoré ešte nemá backup target.
- **Enabled** – zaradenie zariadenia do zálohovania.
- **Policy override** – backup policy určujúca plán, retry a spôsob ukladania.
  Bez priradenej povolenej policy nevznikne automatický harmonogram; manuálny
  test a backup možno spustiť aj bez nej.
- **Local retention profile** – lokálna retenčná výnimka. Prázdne pole použije
  profil backup policy a potom Local-storage fallback.
- **Remote retention profile** – retenčná výnimka pre vzdialené kópie. Prázdne
  pole použije profil každého vzdialeného storage samostatne.
- **Credential override** – login namiesto profilu z platform mappingu.
- **Connection override** – connection profile namiesto profilu z mappingu.
- **Receiver override** – device upload receiver iba pre driver, ktorý ho potrebuje.
- **Driver override** – driver namiesto drivera priradeného platforme.
- **Driver options override** – modelovo špecifické voľby vo formáte JSON;
  používaj iba zdokumentované voľby konkrétneho drivera.
- **Tags** a **Changelog message** – značky a vysvetlenie zmeny pre audit.

Ak má storage zapnuté **Always use this storage's retention profile**, jeho
politika je povinná a tieto voľby zariadenia ju neprepíšu.

Prázdne polia pre driver, credentials, connection a receiver používajú
príslušný profil z povoleného platform mappingu. Port, protokol a SSH overovanie
sa nastavujú v connection profile, nie znova pri každom zariadení. Klikni na
**Save** a potom na detaile na **Test connection**.

### Device-side backup export

Ceragon IP-50 a natívny fallback prvej generácie ALFOplus vyžadujú explicitné
`allow_device_export: true` v driver options. Správca ho nastaví v mappingu
alebo v **Driver options override** až po schválení vytvorenia/exportu
zálohovacieho súboru a príprave receivera. Pri iných natívnych driveroch sa riaď
ich konkrétnymi voľbami; tento súhlas nie je univerzálny príkaz pre všetky modely.
Neznamená súhlas s obnovou konfigurácie ani rebootom.

### Pôvodný Quick Setup

Starší formulár je zachovaný na `/plugins/config-backup/targets/quick-setup/`.
Ponúka **Schedule**, **Local history**, **Remote backup history**, **Advanced
settings**, potvrdenie **Device-side backup export** a **Save & test connection**.
V jednej transakcii pripraví aj potrebné `[Quick]` profily. Nie je to formulár
bežného tlačidla **Devices → Add**. Pre nové nasadenie uprednostni zdieľané
profily a štandardný formulár zhodný s editáciou.

## 9. SSH host keys

Režim sa vyberá v **Connection profile → SSH identity verification** a rovnaká
voľba je dostupná v Advanced settings pri rýchlom pridaní zariadenia.

### Vyžadovať manuálne schválenie

Toto je bezpečné predvolené nastavenie. Prvý test môže skončiť
`HOST_KEY_UNKNOWN` a zobraziť SHA-256 fingerprint:

1. porovnaj fingerprint s konzolou, webovým rozhraním, PuTTY alebo správcom zariadenia,
2. ak sa zhoduje, klikni na **Trust key and test again**,
3. pri nezhode ho neschvaľuj a skontroluj IP/DNS a zariadenie.

### Automaticky dôverovať prvému kľúču (TOFU)

Plugin automaticky prijme iba prvú SSH identitu, ktorá bola kedy zaznamenaná
pre kombináciu backup targetu, adresy a portu. Ak už pre endpoint existuje
pending, trusted alebo rejected história, ďalší kľúč sa automaticky neprijme.
Zmena fingerprintu preto zostane zablokovaná a čaká na manuálne overenie.

### Neoverovať SSH identitu

Plugin sa môže pripojiť bez kontroly host key. Záznamy v zozname dôveryhodných
identít sa nepoužijú, no automaticky sa nevymažú. Tento režim odstráni ochranu
pred zámennou zariadenia a man-in-the-middle útokom. Použi ho iba ako vedomú
výnimku v izolovanej dôveryhodnej manažmentovej sieti.

Hromadný read-only scan je v **Settings → Security and downloads → SSH host keys**.
Scan nepoužíva heslo a nespúšťa konfiguračný príkaz. Profily s vypnutým
overovaním sa pri skene preskočia.

### Výmena alebo starý kľúč

Zmenený fingerprint sa nikdy automaticky neprijme, a to ani v TOFU režime.
Keď administrátor po nezávislom overení schváli náhradu, plugin označí všetky
staršie dôveryhodné kľúče rovnakého targetu, adresy a portu ako **Rejected**.
Starý kľúč sa už nepoužíva, ale riadok zostáva v PostgreSQL ako auditná stopa.
Fyzické automatické mazanie starých SSH identít sa nevykonáva.

## 10. Test connection

Tlačidlo **Test connection** na detaile backup zariadenia otvorí priamo v
plugine stránku s animovaným priebehom. Po dokončení zostáva používateľ v
plugine a vidí:

- výsledný stav,
- bezpečný popis chyby a error code,
- použitý driver,
- počet overených artifactov a prijatú veľkosť,
- údaje o čase testu,
- prípadný host-key fingerprint na schválenie.

Odkaz **Background task** je iba doplnkový technický detail.

Úspešný test neznamená iba otvorený TCP port. Driver sa prihlási, vykoná
zálohovací zber a overí prijaté dáta. Test však nevytvorí ConfigRevision ani
nepotvrdzuje uloženie do Local storage alebo reálny prenos na vzdialené úložisko.

## 11. Manuálna a automatická záloha

Na detaile zariadenia klikni na **Run backup**. Záznam sa objaví v **Runs** a
spracuje ho dedikovaný worker. Automatické zálohy vytvára dispatcher podľa
`next_run_at` a backup policy.

### Stavy runu

| Stav | Význam |
| --- | --- |
| Queued | Run čaká vo fronte. |
| Running | Worker ho spracúva. |
| Success (changed) | Zber prešiel a normalizovaný obsah sa zmenil. |
| Success (unchanged) | Zber prešiel, ale logická konfigurácia sa nezmenila. |
| Partial | Získala sa iba časť očakávaných dát. |
| Failed | Očakávaná chyba pripojenia, autentifikácie, drivera alebo úložiska. |
| Errored | Neočakávaná interná chyba. |
| Skipped | Run sa vedome nevykonal, napríklad pre stav targetu alebo konflikt. |

Pri každom neúspechu otvor detail runu. `Error code` je vhodný na filtrovanie a
`Error message` obsahuje bezpečný dôvod bez hesla.

### Changed a raw changed

- **Changed** porovnáva normalizovanú konfiguráciu a ignoruje známe volatilné údaje.
- **Raw changed** znamená, že sa zmenili prijaté bajty, aj keď logická konfigurácia ostala rovnaká.

Backup policy určuje **Store mode**:

- **Changed configurations only** – pri nezmenenej konfigurácii nevytvorí ďalšiu revision,
- **Every successful collection** – uloží revision po každom úspešnom zbere.

Aj pri `Success (unchanged)` plugin overí kópiu poslednej revision na každom
povolenom vzdialenom storage so zapnutým automatickým kopírovaním. Chýbajúcu
alebo poškodenú kópiu zaradí na opravu z uložených dát, bez ďalšieho pripájania
k zariadeniu. Kópie zámerne odstránené retenciou sa takto znova nevytvárajú.

## 12. Zdravie zariadení

| Stav targetu | Význam |
| --- | --- |
| Never backed up | Zatiaľ neexistuje úspešná záloha. |
| Healthy | Posledná záloha je úspešná a stále v očakávanom čase. |
| Stale | Chýba úspešná záloha v čase očakávanom podľa schedule a grace period. |
| Failed | Posledný relevantný pokus zlyhal. |
| Disabled | Target sa automaticky nespúšťa. |

**Stale target** a **stuck run** nie sú rovnaký problém:

- stale opisuje chýbajúci úspech zariadenia podľa harmonogramu,
- stuck opisuje konkrétny run, ktorý zostal príliš dlho v `Queued` alebo `Running`.

Na detaile zariadenia sú odkazy na posledné runs, revisions a možnosť
**Recalculate schedule**.

## 13. Revisions a artifacty

ConfigRevision obsahuje hash, driver, čas, príznak zmeny a jeden alebo viac
artifactov. Textová konfigurácia môže byť primárny artifact; pri natívnom
backupe môžu byť uložené aj ZIP, TGZ alebo manifest.

### View configuration

Náhľad je určený na čítanie a porovnanie. Plugin maskuje rozpoznané citlivé
priradenia iba v prehliadači; uložený artifact nemení. Prístup k náhľadu a
downloadu preto povoľ iba vybraným skupinám.

Pred zobrazením sa kontroluje veľkosť, SHA-256 a podporovaný textový formát.
Ak súbor chýba, web proces ho nevie čítať alebo kontrola integrity zlyhá, plugin
náhľad odmietne. Binárny natívny backup nemusí mať textový náhľad.

### Download artifacts

Oprávnený používateľ môže stiahnuť každý artifact revision, nielen natívny
backup. Sťahovanie pred odovzdaním overuje uloženú veľkosť a SHA-256.

Stiahnutý artifact je pôvodný súbor, nie redigovaný náhľad, a môže obsahovať
citlivé údaje. Ak je zapnutá ochrana sťahovania, dostaneš ho v ZIPe chránenom
heslom podľa nasledujúceho postupu.

### ZIP chránený heslom

1. Ako správca otvor **Settings → Security and downloads → Protected ZIP downloads**.
2. Zapni **Require a password for downloaded ZIP files**.
3. Vyplň **New ZIP password** a **Confirm new ZIP password**. Minimum je 12
   znakov; použi silné náhodné heslo a odovzdaj ho oprávneným osobám oddelene.
4. Klikni na **Save download protection** a over jedno stiahnutie aj rozbalenie.

Heslo sa ukladá šifrované pomocou master key a po uložení sa nezobrazuje.
Prázdne heslové polia zachovajú existujúce heslo. Zmena platí iba pre nové
sťahovania; súbory v Local, FTP, NFS a SMB3 storage sa neprepisujú. Pred
odstránením uloženého hesla najprv vypni ochranu a ulož zmenu.

Download používa **WinZip AES-256**. Otváraj ho kompatibilným archivačným
nástrojom, napríklad 7-Zip; vstavané rozbaľovanie vo Windows Prieskumníkovi
tento AES formát nepodporuje. Chyba rozbalenia v Prieskumníkovi preto sama
neznamená poškodenú zálohu. Pri chýbajúcom hesle alebo master key plugin
sťahovanie odmietne, namiesto toho, aby vydal nechránený súbor.

Jednotlivý artifact je jediným súborom v chránenom ZIPe. Recovery balík z FTP
obsahuje overené súbory, manifest a README priamo v jednom šifrovanom ZIPe,
bez ďalšieho obalového ZIPu. Natívny ZIP/TGZ od výrobcu sa však nerozbaľuje:
jeho pôvodné bajty musia zostať zachované pre manuálnu obnovu.

### Compare

Porovnanie dvoch textových revisions zobrazí unified diff. Pri binárnych
artifactoch sa porovnáva dostupný textový/štruktúrovaný artifact, ak ho driver
vytvoril.

### Protect revision

Chránenú revision retention neodstráni. Použi ochranu pre dôležitý stav pred
zmenou, incidentom alebo upgradeom. Po skončení potreby ju možno odomknúť.

### Delete everywhere

Na detaile nechráneného záznamu môže oprávnený správca použiť **Delete
everywhere**. Pred potvrdením uvidí lokálne artifacty a všetky evidované
vzdialené kópie. Ak beží backup/prenos, storage je vypnuté alebo nemožno jeho
súbory bezpečne odstrániť, operácia sa zablokuje alebo skončí chybou; neobchádzaj
ju ručným mazaním riadkov v databáze.

Po úspešnom potvrdení sa odstránia súbory a súvisiace revision/artifact/replica
záznamy z pluginu. Na rozdiel od automatickej retencie možno takto výslovne
odstrániť aj poslednú revision. Existujúce **BackupRun** záznamy zostávajú ako
história pokusov, len ich odkaz na revision sa vyprázdni. Na zariadenie sa nič
neposiela a vzdialené vymazanie nie je vratné.

### Záložka Backup na NetBox zariadení

Otvor bežné NetBox zariadenie a jeho záložku **Backup**. Obsahuje najnovšiu
dostupnú revision, posledných 25 viditeľných revisions s akciami **View** a
**Download**, aj nedávne backup runs. Cez **All revisions**, **All runs** a
**Backup settings** sa dostaneš k celej histórii alebo nastaveniu targetu.

Po vymazaní poslednej revision uvidíš **No stored backup**. Úspešný historický
run zostáva úspešný, ale jeho odstránená revision je označená **Revision
removed**. Čas posledného úspechu a nasledujúceho plánu sa nemaže: opisuje
históriu zberu, nie existenciu súboru. Revisions, ku ktorým používateľ nemá
oprávnenie, sa neoznačujú ako vymazané.

## 14. Storages a vzdialené kópie

Sekcia **Config Backup → Storages** obsahuje presne jedno systémové **Local
storage**. Reprezentuje primárne úložisko nakonfigurované cez `storage_root`, je
vždy povolené a nedá sa zmazať, vypnúť ani zmeniť na vzdialené storage. Administrátor na ňom
môže vybrať iba lokálnu retenčnú politiku a rozhodnúť, či má byť povinná pre
všetky zariadenia.

FTP je v tomto nasadení sekundárna interná kópia. Úspech zálohy zariadenia sa
určuje po uložení do primárneho lokálneho úložiska. Výpadok FTP preto nemení
úspešný device backup na failed; FTP kópia má samostatný stav a retry.

FTP prenáša používateľské meno, heslo aj konfiguráciu bez šifrovania. Použi ho
iba v izolovanej dôveryhodnej internej sieti a obmedz FTP účet na určený adresár.

NFS a SMB3 sa nepripájajú priamo z pluginu. Správca najprv pripojí share na
hostiteľovi alebo cez Docker a rovnakú absolútnu cestu sprístupní NetBox webu,
bežnému workeru aj backup workeru. Plugin pred operáciou overí, že cesta je
skutočný aktívny mount. Ak share vypadne, odmietne zápis, aby záloha neskončila
omylom na lokálnom filesystéme kontajnera. Používa sa iba aktuálne SMB3; SMB1
sa nepodporuje.

### Vytvorenie FTP storage

1. V **Settings → Device defaults → Credential profiles** vytvor password credential pre FTP účet.
2. Otvor **Config Backup → Storages → Add** a vyber typ **FTP (unencrypted)**.
3. Vyplň názov, host, port, base path a credential profile.
4. Potvrď, že ide o nešifrované FTP v internej sieti.
5. Podľa potreby vyber vzdialený retenčný fallback. Prepínač **Always use this
   storage's retention profile** zapni iba vtedy, keď zariadenia nesmú túto
   politiku prepísať.
6. Nastav **Copy new revisions automatically** podľa potreby.
7. Ulož storage a klikni na **Test storage**.

Test vytvorí malý súbor, prečíta ho späť, porovná obsah a odstráni ho. Úspešný
test preto overuje spojenie, login, zápis, čítanie aj mazanie.

### Vytvorenie NFS alebo SMB3 storage

1. Správca pripojí NFS export alebo SMB3 share podľa
   [návodu pre NFS a SMB3](docs/NFS_AND_SMB3_STORAGE.md).
2. Rovnaký mount sprístupní NetBox webu a obom workerom, napríklad ako
   `/mnt/netbox-config-backup/nfs`.
3. Otvor **Config Backup → Storages → Add** a vyber **NFS mount** alebo
   **SMB3 / Samba mount**.
4. Zadaj mounted directory a base directory. Meno ani heslo sa do pluginu
   nezadáva; prihlasovanie rieši chránená konfigurácia mountu na hostiteľovi.
5. Ulož storage a spusti **Test storage**.
6. Ak už existujú lokálne revisions, použi **Copy existing revisions** a potom
   **Check stored copies**.

### Adresárová štruktúra

Kópie sa ukladajú pod čitateľným hostname zariadenia:

```text
/<base_path>/devices/<hostname>/backups/<UTC čas vytvorenia>-r<NetBox ID revízie>/
```

Hostname sa upraví na bezpečný názov adresára. Ak názov nie je použiteľný,
plugin použije `device-<id>`. Primárny artifact má čitateľný názov, napríklad
`tn.ps.kos.sw01_2026-08-26_08-11-06.txt`; prípona zostáva podľa formátu
konkrétneho artifactu. Globálne jedinečné NetBox ID revízie je pripojené ku
časovému adresáru, takže súbor zostáva čitateľný, dve revisions vytvorené v
rovnakej sekunde sa neprepíšu a cesta nie je zbytočne predĺžená o ďalší UUID
adresár. UUID revízie zostáva v integritnom manifeste.

Vzdialená cesta sa po vytvorení nemení. Premenovanie zariadenia v NetBoxe
nepremiestni staré kópie; audit, oprava, recovery aj retencia použijú cestu
uloženú pri replica zázname. Plugin naďalej podporuje aj staršie rozloženia
`.../backups/<UTC čas>/` bez ID, prechodný vnorený formát
`.../backups/<UTC čas>/<revision UUID>/` a pôvodný
`.../revisions/<revision UUID>/`.

### Existujúce a chýbajúce kópie

- **Copy existing revisions** zaradí doterajšie revisions, ktoré ešte pre tento cieľ nemajú replica záznam.
- Neúspešnú kópiu možno spustiť cez **Retry**.
- Pri úspešnom nezmenenom backupe plugin overí poslednú kópiu na každom povolenom storage s automatickým kopírovaním a chýbajúcu alebo poškodenú kópiu zaradí na opravu.
- Súbory revisions sú zapisované ako immutable; plugin existujúci odlišný obsah potichu neprepíše.

Retenčné čistenie odstráni staré kópie iba vtedy, keď pre konkrétnu dvojicu
zariadenie–storage existuje efektívny vzdialený retenčný profil a správca manuálne
spustí remote cleanup alebo osobitne zapne remote retention scheduler. Profil môže
pochádzať zo storage alebo zo zariadenia podľa priority opísanej v kapitole 17.
Ak ho nemá ani jedno z nich, kópie na danom storage sa uchovávajú navždy.
Serverová retencia alebo snapshoty NAS môžu slúžiť ako ďalšia nezávislá ochrana.

Explicitné **Delete everywhere** na revision alebo odstránenie celého backup
targetu je od retencie oddelená, potvrdzovaná operácia.

Na existujúcej inštalácii pred prvým zapnutím remote cleanupu spusti read-only
integrity audit na každom vzdialenom storage. Najprv vyrieš všetky chýbajúce alebo
poškodené historické kópie, ktoré plugin eviduje ako úspešné.

### Stored revisions – čo je na úložisku

Na detaile FTP, NFS alebo SMB3 storage otvor **Stored revisions**. Tabuľka
obsahuje evidované revisions, zariadenie, čas vytvorenia, stav prenosu,
dostupnosť, veľkosť a vzdialenú cestu. Podporuje vyhľadávanie podľa zariadenia,
UUID alebo cesty, filter **Copy state** a stránkovanie.

Ide o evidenciu kópií pluginu, nie o všeobecný prehliadač všetkých súborov na
serveri. **Available** opisuje posledný evidovaný stav; prítomnosť a obsah
súborov aktuálne overíš cez **Check stored copies**. Samotný adresár alebo
úspešný test storage nie je dôkaz, že konfigurácia bola prenesená celá.

## 15. Integrity audit vzdialeného storage

Na detaile FTP, NFS alebo SMB3 storage možno spustiť **Check stored copies** alebo zapnúť
**Run integrity audits automatically** denne alebo týždenne.

Audit je read-only. Pre úspešné replica záznamy kontroluje:

- existenciu očakávaných súborov,
- veľkosť,
- SHA-256 hash.

Audit nič nenahráva, nepremenúva ani nemaže. Výsledok ukáže počet
zdravých kópií, poškodených/chýbajúcich kópií a skontrolovaných súborov.
Zlyhanie a následné zotavenie môžu vytvoriť NetBox alert.

## 16. Overený recovery ZIP z FTP

Na detaile revision časť **Verified FTP copies** umožňuje pripraviť dočasný ZIP
z konkrétnej FTP kópie.

Worker:

1. stiahne presne vybranú revision z FTP,
2. overí veľkosť a SHA-256 všetkých súborov,
3. pridá informačný README,
4. vytvorí časovo obmedzený ZIP na stiahnutie.

Táto operácia sa nepripája k zariadeniu a nič naň neposiela. Obnovu z artifactu
vykonáva správca manuálne podľa postupu výrobcu.

## 17. Lokálna a vzdialená retencia

Plugin používa pre zariadenie jeden lokálny plán a samostatný vzdialený plán pre
**každé FTP, NFS alebo SMB3 storage**. Lokálny plán obmedzuje rast PostgreSQL
záznamov a primárneho artifact úložiska. Každý vzdialený plán rozhoduje iba o
kópiách revisions na jednom konkrétnom storage. Zmena alebo spustenie jedného plánu
automaticky nespustí druhý.

### Nastavenie a presná priorita

Politika nastavená na storage je predvolene **fallback**: použije sa iba vtedy,
keď vyššia vrstva nemá vlastnú voľbu. Prepínač **Always use this storage's
retention profile** z nej spraví povinnú politiku a výnimku zariadenia ignoruje.

Efektívna lokálna politika sa vyberá v tomto presnom poradí:

1. povinná politika Local storage,
2. Local retention override priamo na zariadení,
3. retenčná politika z backup policy zariadenia,
4. fallback politika Local storage,
5. bez politiky – uchovanie bez časového limitu.

Efektívna vzdialená politika sa vyberá **samostatne pre každé vzdialené storage**:

1. povinná politika daného storage,
2. Remote retention profile priamo na zariadení,
3. fallback politika daného storage,
4. bez politiky – kópie na tomto storage sa uchovávajú bez časového limitu.

Štandardný formulár zariadenia ponúka **Local retention profile** a **Remote
retention profile** ako device overrides. Prázdna voľba neobchádza storage fallback.
Na detaile a v retention preview je viditeľný efektívny profil aj jeho zdroj:
**Storage enforced**, **Device override**, **Backup policy**, **Storage default**
alebo **Keep indefinitely**.

Priradenie alebo zmena retenčného profilu vyžaduje administrátorské práva na
príslušné mazanie. Operator môže meniť plán záloh, ale nemôže nepriamo zapnúť
agresívnejšie mazanie histórie.

### Lokálny retenčný profil

Pre revisions používa tieto pravidlá:

- **Keep all days** – zachová všetky revisions z posledných dní,
- **Daily days** – zachová najnovšiu revision za deň,
- **Weekly weeks** – zachová najnovšiu revision za ISO týždeň,
- **Monthly months** – zachová najnovšiu revision za kalendárny mesiac,
- **Minimum changed revisions** – vždy ponechá minimálny počet zmenených revisions.

Lokálny profil navyše riadi históriu runs:

- **Unchanged run days** – uchovanie úspešných nezmenených pokusov,
- **Changed run days** – uchovanie úspešných zmenených pokusov,
- **Failed run days** – uchovanie failed, errored, partial a skipped pokusov,
- **Max runs per target** – tvrdý strop dokončených runs na zariadenie; predvolene 500.

Aktívne `Queued` a `Running` záznamy sa limitom nemažú. Lokálny cleanup nemaže
vzdialenú kópiu, ktorá má zostať zachovaná podľa vzdialeného plánu.

### Vzdialený retenčný profil

Remote profil používa samostatné **Keep all**, denné, týždenné a mesačné okná,
minimálny počet zmenených revisions a **Maximum remote revisions per device**.
Tento `max_copies_per_target` limit sa počíta **pre jedno zariadenie na jednom
vzdialenom storage**. Jednotlivé fyzické artifact súbory v revision sa nepočítajú
samostatne. Tá istá revision na dvoch storages však spotrebuje jednu pozíciu
v každom z ich nezávislých plánov. Profil neodstraňuje BackupRun záznamy ani
lokálne artifacty. Najnovšia revision a revisions označené ako **protected** sa
zachovajú v príslušnom lokálnom aj vzdialenom rozsahu aj vtedy, keď by prekročili bežné časové okno alebo
limit počtu kópií.

Odstránenie vzdialenej kópie je nevratná operácia voči danému storage. Plugin sa
pri nej nepripája k zariadeniu, nemení jeho konfiguráciu a nevykonáva automatický
restore. Pred zapnutím mazania preto over aj nezávislé NAS snapshoty alebo inú
recovery kópiu, ak ich prevádzka vyžaduje.

Rozpracované `Pending`, `Queued`, `Running` a neúspešné kópie čakajúce na retry
sa nemažú. Ak však retry už bolo vyčerpané a replica má uloženú presnú vzdialenú
cestu, cleanup ju bezpečne skontroluje a odstráni. Na tejto ceste totiž môže
zostať staršia úplná kópia alebo časť neúspešnej opravy. Vypnuté vzdialené storage je
kill switch: jeho kópie cleanup ani mazanie zariadenia nemenia, kým správca cieľ
znova nepovolí.

Kým revision zostáva v histórii pluginu, metadata o odstránenej vzdialenej kópii
bránia jej nechcenému opätovnému vytvoreniu. Keď neskôr úplne vyprší lokálna
revision aj všetky jej vzdialené kópie, cleanup môže odstrániť aj revision a príslušné
replica/deletion audit metadata. Tieto metadata nie sú trvalý auditný archív.

### Preview a manuálne spustenie

1. Na detaile zariadenia otvor **Retention preview**.
2. Samostatne skontroluj **Local storage and run history** a plán každého
   vzdialeného storage v časti **Remote copies**.
3. Dôležité revisions najprv označ ako **protected**.
4. Použi **Apply local retention** alebo **Apply remote retention**. Každá operácia má vlastné potvrdenie a pred vykonaním plán znovu prepočíta.

Preview je iba na čítanie. Ak pre konkrétnu dvojicu zariadenie–storage nie
je efektívny profil ani na zariadení, ani na storage, zobrazí **Keep
indefinitely** a remote cleanup túto dvojicu preskočí.

### Automatické schedulery

V **Settings → Automation** sú dva samostatné prepínače: **Enable Local cleanup**
a **Enable remote cleanup**. Oba sú po inštalácii predvolene vypnuté, každý vyžaduje
vlastné potvrdenie trvalého mazania a po zapnutí sa vyhodnocuje každých 24 hodín.
Zmenu potvrď cez **Save cleanup settings**. Zapnutie lokálneho schedulera
nezapne vzdialený scheduler a naopak. Vzdialený scheduler preskočí každú
dvojicu zariadenie–storage bez efektívneho retenčného profilu. Jedno zariadenie
preto môže mať cleanup na jednom storage a uchovanie navždy na inom.

## 18. NetBox alerts

V **Settings → Automation** možno zapnúť udalosti pre:

- prvé zlyhanie a recovery zariadenia,
- stale target,
- stuck run,
- zlyhanie a recovery FTP kopírovania,
- zlyhanie a recovery FTP integrity auditu.

Plugin udalosti vytvorí, ale príjemcov určuje NetBox cez **Event Rules** a
**Notification Groups**. Predvolene sa opakované rovnaké zlyhanie neoznamuje pri
každom pokuse; správanie možno zmeniť v **Repeated failure behavior**.

## 19. Oprávnenia

Pripravené skupiny:

| Skupina | Úloha |
| --- | --- |
| Config Backup Readers | Čítanie stavov, revisions, redigovaného náhľadu a diffov. |
| Config Backup Operators | Čítanie konfigurácie pluginu, testovanie, spúšťanie záloh, schedule a ochrana revisions. |
| Config Backup Administrators | Úplná správa pluginu vrátane credentials, mappingov, driverov, ochrany downloadov, retencie a úložísk. |

Skupiny vytvorí správca príkazom:

```shell
python manage.py config_backup_create_rbac_groups
```

Príkaz nepriradí žiadneho používateľa automaticky. Používateľov treba do skupín
zaradiť vedome. Po nastavení vždy otestuj nesuperuser účet a potvrď, že obsah a
download revisions vidia iba vybrané osoby.

## 20. Podporované drivery

Balík obsahuje najmä tieto drivery; ich dostupnosť pre nové priradenia riadi
**Settings → Device drivers**:

| Rodina | Driver / spôsob |
| --- | --- |
| Cisco IOS, IOS-XE | read-only CLI cez SSH/Netmiko |
| MikroTik RouterOS | `/export terse hide-sensitive` cez SSH |
| Dell OS6/OS9/OS10/PowerConnect | read-only CLI |
| FS/Fiberstore FSOS | read-only CLI |
| HP/HPE Comware, HP/Aruba ProCurve | read-only CLI |
| Huawei VRP/VRPv8 | read-only CLI |
| TP-Link JetStream | read-only CLI |
| Ubiquiti EdgeRouter/EdgeSwitch | read-only CLI |
| ZTE ZXROS | read-only CLI |
| RACOM RipEX2 | HTTPS API |
| RACOM RAy2/RAy3 | natívny SSH/SCP backup |
| Ceragon IP-20 | stiahnutie natívnej zálohy zo zariadenia cez SFTP; potrebuje overený `remote_path`, nie receiver |
| Ceragon IP-50 / CeraOS | vytvorenie restore pointu a odoslanie natívnej zálohy na SFTP device upload receiver |
| SIAE SM-OS | automatický driver; CLI snapshot alebo nakonfigurovaný natívny fallback |
| Fake | iba vývoj a automatické testy, nie produkcia |

Nie každý model a firmware výrobcu sa správa rovnako. Pred hromadným nasadením
otestuj jeden reprezentatívny kus každej platformy a verzie firmvéru.

### Device upload receivers

Niektoré zariadenia zálohu posielajú smerom k pluginu. Pre ne administrátor
nastaví **Settings → Security and downloads → Device upload receivers**. Ide o inú
funkciu než FTP storage:

- device upload receiver prijíma súbor priamo zo zariadenia počas zberu,
- FTP storage kopíruje už dokončenú revision z pluginu na interný server.

Receiver potrebuje **Ceragon IP-50/CeraOS** a voliteľný natívny WebLCT/FTP
fallback pre **ALFOplus prvej generácie**. Bežné CLI drivery, RACOM, Ceragon
IP-20 a SIAE používajúce `show running-config` ho nepotrebujú. Zapnutie drivera
v Settings receiver automaticky nenainštaluje ani nespustí; nasadenie opisuje
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Prvá generácia SIAE ALFOplus môže vyžadovať výrobcovský WebLCT/SCT workflow a
nemusí podporovať úplný `show running-config`. Ak automatický driver oznámi
`COMMAND_UNSUPPORTED`, nepovažuj ľubovoľný shell výpis za úplnú konfiguráciu.

## 21. Najčastejšie chyby

| Error code | Čo skontrolovať |
| --- | --- |
| `NO_ADDRESS` | OOB alebo primárnu IP v NetBoxe a dostupnosť z workera. |
| `CONNECTION_REFUSED` | Správny protokol, port a zapnutú službu na zariadení. |
| `TIMEOUT` | Routing/VPN, firewall, IP, port a connect timeout. |
| `AUTH_FAILED` | Credential profile, používateľské meno, heslo a povolený spôsob loginu. |
| `HOST_KEY_UNKNOWN` | V manuálnom alebo TOFU režime over nový fingerprint; zmenený kľúč schváľ iba po nezávislom potvrdení. |
| `HOST_KEY_FAILED` / `HOST_KEY_MISMATCH` | Zariadenie mohlo vymeniť kľúč alebo ide o inú IP; neschvaľuj bez overenia. |
| `COMMAND_UNSUPPORTED` | Model alebo firmware nepodporuje príkaz drivera; potrebuje iný bezpečný workflow. |
| `DRIVER_DISABLED` | Potrebný driver musí byť povolený v Settings → Device drivers. |
| `DRIVER_SETUP_REQUIRED` | Chýba modelovo špecifická voľba, napríklad overený remote path; riaď sa požiadavkou konkrétneho drivera. |
| `INCOMPLETE_CONFIG` | Výstup neobsahuje úplnú konfiguráciu; skontroluj driver, oprávnenia a paging. |
| `NO_CREDENTIAL_PROFILE` | Mapping/target nemá kompletný credential profile. |
| `NO_RECEIVER_PROFILE` | Natívny driver nemá povolený receiver. |
| `DEVICE_EXPORT_FAILED` | Export bol odmietnutý alebo sa zariadenie nepripojilo k receiveru. |
| `DESTINATION_TEST_FAILED` | FTP login, práva na zápis/čítanie/mazanie, base path a passive firewall. |
| `STORAGE_FAILED` | Práva na zápis do Local storage, voľné miesto a správny perzistentný volume v backup workeri. |
| `INTERNAL_ERROR` | Otvor Background task a log web/worker procesu; môže ísť o chybu pluginu. |

Ak test prejde, ale backup zlyhá, porovnaj konkrétny BackupRun s testom. Natívny
backup môže trvať dlhšie, vytvárať súbor, používať reverse tunnel alebo vyžadovať
ďalšiu komunikáciu smerom k receiveru.

### Run zostáva Queued alebo test Pending

Skontroluj živý dedikovaný worker počúvajúci na `netbox_config_backup.backup`,
nie iba existenciu kontajnera. Táto queue spracúva zálohy, connection/storage
testy, kopírovanie na vzdialené úložiská, integrity audity, cleanup aj FTP
recovery balíky. Bežný NetBox worker musí zostať spustený pre systémové
plánovacie úlohy a ostatnú prácu NetBoxu.

Pri novej požiadavke na backup plugin kontroluje dostupnosť workera. Ak žiadny
živý worker nepočúva, požiadavku odmietne bez vytvorenia ďalšieho runu. Už
zaradený čakajúci run možno bezpečne zrušiť na jeho detaile; zostane ako
**Skipped**. Dispatcher tiež zosúlaďuje staré osirelé úlohy so stavom Redis.
Nemaž ručne náhodné joby alebo databázové riadky. Najprv vyrieš worker a
konkrétny blokujúci run.

### Backup existuje, ale náhľad sa nedá otvoriť

Skontroluj, že web aj workery používajú ten istý Local storage volume na tej
istej ceste. Úspešný zápis v backup workeri nepotvrdzuje, že súbor vidí aj web.
Over práva účtu NetBox na čítanie a výsledok integrity kontroly; bez týchto
kontrol plugin konfiguráciu nezobrazí.

### V Settings chýba Device drivers alebo vidno staré rozloženie

Nové rozloženie a driver selection musia byť v skutočne nainštalovanom balíku,
nielen v lokálnom repozitári. Over aktualizáciu webu aj workerov a vykonanie
migrácie `0030_operationalsettings_disabled_driver_ids`. Po aktualizácii kódu
reštartuj alebo vytvor kontajnery z nového image podľa inštalačného návodu;
reštart starého image novú verziu nenainštaluje.

Web môže držať staré šablóny v pamäti aj po zmene súborov v bind mounte. Ich
načítanie vyrieši reštart web procesu; obyčajný refresh prehliadača nie. Pre
staré CSS následne použi **Ctrl+F5**. Ak nevidíš ani možnosť ukladania, over aj
oprávnenie na zmenu Settings. Bežné ukladanie UI volieb ďalší reštart nepotrebuje.

## 22. Bezpečnostné zásady

- Plugin spúšťaj z oddelenej management siete.
- Používaj samostatné účty s minimálnymi právami.
- V produkcii používaj manuálne schválenie SSH identity a fingerprint overuj nezávislým kanálom.
- TOFU používaj iba pri kontrolovanom prvom nasadení; vypnuté overovanie iba ako zdokumentovanú výnimku v izolovanej sieti.
- Telnet a FTP povoľ iba v izolovanej dôveryhodnej sieti.
- Master key a PostgreSQL zálohuj oddelene, ale obnovuj ich spolu.
- Artifact volume musí byť perzistentné a prístupné iba NetBox service účtu.
- Obsah revisions a download povoľ iba vybraným skupinám.
- Do logov a changelog správ nevkladaj heslá ani privátne kľúče.
- Pravidelne testuj recovery ZIP a manuálny výrobcovský restore postup.
- Nezamieňaj úspešný connection test s potvrdením, že retention, FTP a obnova sú správne nastavené.

Podrobnosti sú v [SECURITY.md](SECURITY.md).

## 23. Odporúčaný prevádzkový postup

### Pri novom type zariadenia

1. Over platformu a management IP v NetBoxe.
2. Vytvor alebo vyber least-privilege účet.
3. Priprav connection profile a credential profile.
4. Over povolenie drivera v **Settings → Device drivers** a vytvor platform mapping.
5. Pridaj jeden testovací kus.
6. Vyber režim SSH identity; pri manuálnom režime over a schváľ host key.
7. Spusti connection test.
8. Spusti manuálny backup.
9. Otvor revision, náhľad a artifact download.
10. Over skutočné súbory vo **Stored revisions** a integrity audit vzdialeného storage.
11. Až potom pridaj ďalšie zariadenia rovnakej platformy.

### Pravidelná kontrola

- denne sleduj Overview a nové failed/stale/stuck stavy,
- kontroluj stav vzdialených úložísk a neúspešné replicas,
- pravidelne vyhodnocuj automatic integrity audit,
- pred zapnutím alebo zmenou retention použi preview,
- po upgrade otestuj aspoň jeden connection test, reálny backup, FTP copy a recovery ZIP,
- pravidelne over nesuperuser oprávnenia.

## 24. Odstránenie zariadenia z pluginu

Odstránenie backup targetu nevymaže NetBox zariadenie. Plugin pred potvrdením
zobrazí súvisiace runs, revisions a artifacty, ktoré budú zasiahnuté. Hromadné
odstránenie používa rovnakú kontrolu.

Pred odstránením over, či sú dôležité revisions chránené alebo bezpečne
exportované. Plugin odstráni aj evidované FTP, NFS a SMB3 kópie. Ak je storage vypnuté,
prebieha prenos alebo vzdialenú kópiu nemožno bezpečne odstrániť, odstránenie
celého backup targetu zablokuje.

## 25. Dokumenty pre administrátora

- [Inštalácia](docs/INSTALLATION.md)
- [Nasadenie a workery](docs/DEPLOYMENT.md)
- [Kompatibilita](COMPATIBILITY.md)
- [Bezpečnosť](SECURITY.md)
- [Rotácia master key](docs/MASTER_KEY_ROTATION.md)
- [FTP storage a recovery](docs/FTP_DESTINATION.md)
- [NFS a SMB3 storage](docs/NFS_AND_SMB3_STORAGE.md)
- [README a prehľad funkcií](README.md)
- [Zmeny podľa vydania](CHANGELOG.md)

Po každej aktualizácii kódu pluginu musí rovnaký balík/image používať web proces,
bežný NetBox worker aj dedikovaný backup worker; pri nasadenom receiveri aj jeho
proces. Použi postup aktualizácie v inštalačnom návode: priprav nový balík/image,
vykonaj migrácie, `collectstatic` a kontrolu, potom reštartuj alebo vytvor
príslušné služby z aktualizovaného image. Nestačí iba vymeniť súbory pod
bežiacim webom alebo reštartovať kontajner so starým image.

Výber driverov pridáva migrácia `0030_operationalsettings_disabled_driver_ids`.
Vykoná sa štandardným `manage.py migrate`; nepreskakuj ju. Zachová všetky
aktuálne dostupné drivery povolené a nemení uložené zálohy. Naproti tomu zmena
už dostupných UI nastavení cez ich Save tlačidlo nevyžaduje migráciu ani reštart.
