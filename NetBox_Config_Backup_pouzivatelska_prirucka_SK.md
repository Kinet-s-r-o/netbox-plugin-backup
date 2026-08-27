# NetBox Config Backup – používateľská príručka

Táto príručka opisuje bežnú prevádzku pluginu **NetBox Config Backup 0.6.x**
v NetBoxe 4.6. Je určená operátorom aj správcom, ktorí potrebujú bezpečne
zálohovať konfigurácie sieťových zariadení, kontrolovať výsledky a udržiavať
históriu.

Štandardné nasadenie používa lokálne úložisko pluginu ako primárnu kópiu a
voliteľný interný FTP server ako druhú kópiu. Inštaláciu a serverové nastavenia
opisuje samostatný dokument [docs/INSTALLATION.md](docs/INSTALLATION.md).

## 1. Čo plugin robí

Pri každom pokuse o zálohu plugin:

1. vytvorí záznam **BackupRun**,
2. určí adresu, driver, connection profile a credentials,
3. pripojí sa k zariadeniu,
4. načíta konfiguráciu alebo natívny zálohovací súbor,
5. skontroluje, že získané dáta zodpovedajú očakávanému formátu,
6. vypočíta SHA-256 hash,
7. pri potrebe vytvorí **ConfigRevision** a uloží jej artifacty,
8. samostatne vytvorí kópiu revision na povolenom FTP storage.

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
| FTP storage | Samostatné sekundárne úložisko dokončených revisions na internom FTP serveri. |
| Lokálny retenčný profil | Pravidlá uchovávania lokálnych revisions, artifactov a BackupRun záznamov; môže byť na zariadení, backup policy alebo Local storage. |
| FTP retenčný profil | Pravidlá uchovávania kópií revisions, ktoré sa vyhodnocujú samostatne pre každé zariadenie a každé FTP storage. |

## 2. Rýchly štart

Predpokladom je nainštalovaný plugin, spustený bežný NetBox worker aj dedikovaný
backup worker a nastavený master key.

1. V **Config Backup → Settings → Credential profiles** vytvor credential profile.
2. V **Settings → Connection profiles** vytvor connection profile.
3. V **Settings → Platform mappings** priraď platformu k driveru a profilom.
4. Otvor **Config Backup → Devices → Add device**.
5. Vyber zariadenie, plán, lokálnu históriu a podľa potreby FTP retenčnú výnimku.
6. Klikni na **Save & test connection**.
7. Pri prvom SSH spojení over fingerprint a schváľ host key.
8. Po úspešnom teste otvor detail zariadenia a klikni na **Run backup**.
9. Skontroluj výsledok v **Runs** a uložený obsah v **Revisions**.
10. Ak používaš FTP, najprv otestuj FTP storage a potom over reálnu kópiu revision.

Formulár **Add device** priraďuje aj lokálnu retenčnú politiku, preto je určený
pre skupinu **Config Backup Administrators**. Operátor môže existujúce ciele
testovať, spúšťať a preplánovať, ale nesmie nepriamo povoliť budúce mazanie.

Prázdna retenčná voľba na zariadení neznamená automaticky „navždy“. Najprv sa
použije fallback politika daného storage; bez politiky na zariadení aj storage
sa história uchováva bez časového limitu.

Zariadenie, ktoré už má backup target, sa v zozname **Add device** znova
nezobrazí.

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

### Storages

Zobrazuje systémové **Local storage** a administrátorom vytvorené FTP storages.
Local storage reprezentuje primárny adresár pluginu, je vždy povolené a nedá sa
zmazať, vypnúť ani zmeniť na FTP. Pri každom storage možno nastaviť retenčný
fallback alebo politikou storage povinne prekryť retenčné nastavenie zariadenia.

### Runs

Každý pokus o zálohu má vlastný záznam. Connection test má samostatnú výsledkovú
stránku a technický NetBox Job. Zoznam runs možno filtrovať podľa
zariadenia, lokality, zdroja, stavu, chyby, zaseknutia a obdobia.

### Revisions

Uložené verzie konfigurácií. Oprávnený používateľ môže zobraziť náhľad,
porovnať verzie, stiahnuť artifact a pripraviť overený balík z FTP kópie.

### Settings

Stránka je rozdelená na tieto časti:

- **Device defaults**: platform mappings, connection profiles a credential profiles,
- **Schedules and retention**: backup policies, Local retention profiles a FTP retention profiles,
- **Security and vendor-specific setup**: SSH host keys a device upload receivers; táto časť je predvolene zbalená,
- **Automation**: samostatné Local a FTP čistenie a NetBox alerts.

Bežné zariadenie sa má dať pridať cez **Add device** bez ručnej tvorby nového
profilu pre každý kus.

### Help

Read-only stránka **Config Backup → Help** vysvetľuje odporúčané poradie
nastavenia, tok zálohy, rozdiel medzi Local a FTP úložiskom, poradie retenčných
pravidiel a prvé kontroly pri bežných error kódoch. Help nezobrazuje heslá ani
aktuálne nasadené secret hodnoty a je dostupný aj skupine Readers.

## 4. Príprava NetBox zariadenia

Plugin potrebuje použiteľnú management adresu. Connection profile určuje jej
poradie:

- **OOB first** – najskôr OOB IP, potom primárna IP,
- **Primary IPv4 first** – uprednostní primárnu IPv4,
- **Primary IPv6 first** – uprednostní primárnu IPv6.

OOB znamená *out-of-band management*: samostatná správcovská adresa alebo sieť,
ktorá nemusí používať produkčnú dátovú cestu. Zvolená adresa musí byť dostupná
z NetBox/backup worker kontajnera, nie iba z počítača používateľa.

Odporúčania:

- nastav na zariadení správnu NetBox platformu,
- eviduj OOB alebo primárnu IP,
- použi samostatný účet určený na zálohovanie,
- prideľ mu iba práva potrebné na čítanie alebo vytvorenie natívnej zálohy,
- over dostupnosť portu z backup workera.

## 5. Credentials a master key

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
- preferenciu OOB/primárnej adresy,
- port,
- connect timeout,
- command timeout,
- keepalive,
- SSH host-key verification a cestu ku `known_hosts`.

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
- podľa potreby native backup receiver,
- obmedzené driver options.

Vďaka mappingu sa pri každom zariadení znova nevyberá výrobca, port ani login.
Ak zariadenie nemá vhodný mapping, v rozšírených nastaveniach **Add device**
možno driver a profily vybrať ručne.

Nový výrobca sa dá doplniť aj externým driver balíkom cez Python entry point.
Samotné pridanie výrobcu alebo platformy do NetBoxu však nevytvorí bezpečný
driver automaticky; driver musí poznať príkaz, formát a validáciu výstupu.

## 8. Pridanie zariadenia

Otvor **Config Backup → Devices → Add device**.

### Základné polia

- **Device** – zariadenie z NetBoxu, ktoré ešte nemá backup target.
- **Credential profile** – zdieľaný login; automatic použije platform mapping.
- **Schedule** – každých 6 hodín, 12 hodín alebo denne o 02:00.
- **Local history** – v rýchlom pridaní vytvorí výnimku zariadenia pre lokálne
  revisions, artifacty a runs. Ak sa výnimka neskôr odstráni, použije sa backup
  policy alebo Local-storage fallback.
- **Remote FTP history** – voliteľná výnimka zariadenia pre FTP kópie. Voľba bez
  počtu dní nepriradí zariadeniu FTP profil: každé FTP storage potom použije
  vlastný fallback a iba storage bez fallbacku uchováva kópie bez časového
  limitu.

Ak má storage zapnuté **Always use this storage's retention profile**, jeho
politika je povinná a tieto voľby zariadenia ju neprepíšu.

### Advanced settings

Rozbaľ ich iba vtedy, keď automatic mapping nestačí. Obsahujú driver, native
receiver, restore point/workspace, connection profile, protokol, port,
host-key kontrolu a dedicated login.

Ak vyberieš credential profile, polia dedicated login sa nepoužijú. Ak vyberieš
connection profile, adresa, port, timeouty a host-key správanie sa prevezmú z
neho.

### Device-side backup export

Pri natívnych driveroch sa zobrazí samostatné potvrdenie. Znamená iba súhlas s
vytvorením alebo nahradením zálohovacieho workspace/súboru a jeho exportom.
Neznamená súhlas s obnovou konfigurácie ani rebootom.

## 9. SSH host keys

Pri zapnutej voľbe **Verify host key** plugin overuje, že sa pripája k správnemu
zariadeniu. Prvý test môže skončiť `HOST_KEY_UNKNOWN` a zobraziť SHA-256
fingerprint.

Bezpečný postup:

1. porovnaj fingerprint s konzolou, webovým rozhraním, PuTTY alebo správcom zariadenia,
2. ak sa zhoduje, klikni na **Trust key and test again**,
3. pri nezhode ho neschvaľuj a skontroluj IP/DNS a zariadenie.

Hromadný read-only scan je v **Settings → Security and vendor-specific setup → SSH host keys**.
Scan nepoužíva heslo a nespúšťa konfiguračný príkaz.

Vypnutie **Verify host key** odstráni ochranu pred zámennou zariadenia a
man-in-the-middle útokom. Má byť iba dočasným diagnostickým krokom v dôveryhodnej
sieti, nie bežným produkčným nastavením.

## 10. Test connection

Tlačidlo **Save & test connection** alebo **Test connection** otvorí priamo v
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
zálohovací zber a overí prijaté dáta. Test však nevytvorí ConfigRevision.

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

Aj pri `Success (unchanged)` plugin overí, či posledná revision má potrebnú FTP
kópiu. Ak bol FTP server vyprázdnený alebo je kópia poškodená, zaradí jej opravu
bez opätovného pripájania k zariadeniu.

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

### Download artifacts

Oprávnený používateľ môže stiahnuť každý artifact revision, nielen natívny
backup. Sťahovanie pred odovzdaním overuje uloženú veľkosť a SHA-256.

### Compare

Porovnanie dvoch textových revisions zobrazí unified diff. Pri binárnych
artifactoch sa porovnáva dostupný textový/štruktúrovaný artifact, ak ho driver
vytvoril.

### Protect revision

Chránenú revision retention neodstráni. Použi ochranu pre dôležitý stav pred
zmenou, incidentom alebo upgradeom. Po skončení potreby ju možno odomknúť.

## 14. Storages a FTP kópia

Sekcia **Config Backup → Storages** obsahuje presne jedno systémové **Local
storage**. Reprezentuje primárne úložisko nakonfigurované cez `storage_root`, je
vždy povolené a nedá sa zmazať, vypnúť ani zmeniť na FTP. Administrátor na ňom
môže vybrať iba lokálnu retenčnú politiku a rozhodnúť, či má byť povinná pre
všetky zariadenia.

FTP je v tomto nasadení sekundárna interná kópia. Úspech zálohy zariadenia sa
určuje po uložení do primárneho lokálneho úložiska. Výpadok FTP preto nemení
úspešný device backup na failed; FTP kópia má samostatný stav a retry.

FTP prenáša používateľské meno, heslo aj konfiguráciu bez šifrovania. Použi ho
iba v izolovanej dôveryhodnej internej sieti a obmedz FTP účet na určený adresár.

### Vytvorenie FTP storage

1. V **Settings → Credential profiles** vytvor password credential pre FTP účet.
2. Otvor **Config Backup → Storages → Add**. Formulár automaticky vytvorí FTP storage.
3. Vyplň názov, host, port, base path a credential profile.
4. Potvrď, že ide o nešifrované FTP v internej sieti.
5. Podľa potreby vyber FTP retenčný fallback. Prepínač **Always use this
   storage's retention profile** zapni iba vtedy, keď zariadenia nesmú túto
   politiku prepísať.
6. Nastav **Copy new revisions automatically** podľa potreby.
7. Ulož storage a klikni na **Test FTP storage**.

Test vytvorí malý súbor, prečíta ho späť, porovná obsah a odstráni ho. Úspešný
test preto overuje spojenie, login, zápis, čítanie aj mazanie.

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
- Pri úspešnom nezmenenom backupe plugin read-only overí poslednú FTP kópiu a chýbajúcu alebo poškodenú kópiu znova vytvorí.
- Súbory revisions sú zapisované ako immutable; plugin existujúci odlišný obsah potichu neprepíše.

Plugin môže staré FTP kópie odstrániť iba vtedy, keď pre konkrétnu dvojicu
zariadenie–FTP storage existuje efektívny FTP retenčný profil a správca manuálne
spustí FTP cleanup alebo osobitne zapne FTP retention scheduler. Profil môže
pochádzať zo storage alebo zo zariadenia podľa priority opísanej v kapitole 17.
Ak ho nemá ani jedno z nich, kópie na danom storage sa uchovávajú navždy.
Serverová retencia alebo snapshoty NAS môžu slúžiť ako ďalšia nezávislá ochrana.

Na existujúcej inštalácii pred prvým zapnutím FTP cleanupu spusti read-only
integrity audit na každom FTP storage. Najprv vyrieš všetky chýbajúce alebo
poškodené historické kópie, ktoré plugin eviduje ako úspešné.

## 15. FTP integrity audit

Na detaile FTP storage možno spustiť **Check stored copies** alebo zapnúť
**Run integrity audits automatically** denne alebo týždenne.

Audit je read-only. Pre úspešné replica záznamy kontroluje:

- existenciu očakávaných súborov,
- veľkosť,
- SHA-256 hash.

Audit na FTP nič nenahráva, nepremenúva ani nemaže. Výsledok ukáže počet
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

## 17. Lokálna a FTP retencia

Plugin používa pre zariadenie jeden lokálny plán a samostatný vzdialený plán pre
**každé FTP storage**. Lokálny plán obmedzuje rast PostgreSQL záznamov a
primárneho artifact úložiska. Každý FTP plán rozhoduje iba o kópiách revisions
na jednom konkrétnom FTP storage. Zmena alebo spustenie jedného plánu
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

Efektívna FTP politika sa vyberá **samostatne pre každé FTP storage**:

1. povinná politika daného FTP storage,
2. FTP retention override priamo na zariadení,
3. fallback politika daného FTP storage,
4. bez politiky – kópie na tomto storage sa uchovávajú bez časového limitu.

Rýchle pridanie zariadenia ponúka voľby **Local history** a **Remote FTP
history** ako device overrides. Prázdna voľba preto neobchádza storage fallback.
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
FTP kópiu, ktorá má zostať zachovaná podľa vzdialeného plánu.

### FTP retenčný profil

FTP profil používa samostatné **Keep all**, denné, týždenné a mesačné okná,
minimálny počet zmenených revisions a **Maximum remote revisions per device**.
Tento `max_copies_per_target` limit sa počíta **pre jedno zariadenie na jednom
FTP storage**. Jednotlivé fyzické artifact súbory v revision sa nepočítajú
samostatne. Tá istá revision na dvoch FTP storages však spotrebuje jednu pozíciu
v každom z ich nezávislých plánov. Profil neodstraňuje BackupRun záznamy ani
lokálne artifacty. Najnovšia revision a revisions označené ako **protected** sa
zachovajú lokálne aj na FTP aj vtedy, keď by prekročili bežné časové okno alebo
limit počtu kópií.

Odstránenie FTP kópie je nevratná operácia voči danému FTP serveru. Plugin sa
pri nej nepripája k zariadeniu, nemení jeho konfiguráciu a nevykonáva automatický
restore. Pred zapnutím mazania preto over aj nezávislé NAS snapshoty alebo inú
recovery kópiu, ak ich prevádzka vyžaduje.

Rozpracované `Pending`, `Queued`, `Running` a neúspešné kópie čakajúce na retry
sa nemažú. Ak však retry už bolo vyčerpané a replica má uloženú presnú FTP
cestu, cleanup ju bezpečne skontroluje a odstráni. Na tejto ceste totiž môže
zostať staršia úplná kópia alebo časť neúspešnej opravy. Vypnuté FTP storage je
kill switch: jeho kópie cleanup ani mazanie zariadenia nemenia, kým správca cieľ
znova nepovolí.

Kým revision zostáva v histórii pluginu, metadata o odstránenej FTP kópii
bránia jej nechcenému opätovnému vytvoreniu. Keď neskôr úplne vyprší lokálna
revision aj všetky jej FTP kópie, cleanup môže odstrániť aj revision a príslušné
replica/deletion audit metadata. Tieto metadata nie sú trvalý auditný archív.

### Preview a manuálne spustenie

1. Na detaile zariadenia otvor **Retention preview**.
2. Samostatne skontroluj **Local storage and run history** a plán každého FTP
   storage v časti **Remote FTP copies**.
3. Dôležité revisions najprv označ ako **protected**.
4. Použi **Apply local retention** alebo **Apply FTP retention**. Každá operácia má vlastné potvrdenie a pred vykonaním plán znovu prepočíta.

Preview je iba na čítanie. Ak pre konkrétnu dvojicu zariadenie–FTP storage nie
je efektívny profil ani na zariadení, ani na storage, zobrazí **Keep
indefinitely** a FTP cleanup túto dvojicu preskočí.

### Automatické schedulery

V **Settings → Automation** sú dva samostatné prepínače: **Enable local cleanup**
a **Enable FTP cleanup**. Oba sú po inštalácii predvolene vypnuté, každý vyžaduje
vlastné potvrdenie trvalého mazania a po zapnutí sa vyhodnocuje každých 24 hodín.
Zapnutie lokálneho schedulera nezapne FTP scheduler a naopak. FTP scheduler
preskočí každú dvojicu zariadenie–FTP storage bez efektívneho retenčného
profilu. Jedno zariadenie preto môže mať cleanup na jednom FTP storage a
uchovanie navždy na inom.

## 18. NetBox alerts

V **Settings → Automation** možno zapnúť udalosti pre:

- prvé zlyhanie a recovery zariadenia,
- stale target,
- stuck run,
- zlyhanie a recovery FTP kopírovania,
- zlyhanie a recovery FTP integrity auditu.

Plugin udalosti vytvorí, ale príjemcov určuje NetBox cez **Event Rules** a
**Notification Groups**. Predvolene sa opakované rovnaké zlyhanie neoznamuje pri
každom pokuse; voľbu možno rozšíriť v Advanced alert behavior.

## 19. Oprávnenia

Pripravené skupiny:

| Skupina | Úloha |
| --- | --- |
| Config Backup Readers | Čítanie stavov, revisions, redigovaného náhľadu a diffov. |
| Config Backup Operators | Čítanie konfigurácie pluginu, testovanie, spúšťanie záloh, schedule a ochrana revisions. |
| Config Backup Administrators | Úplná správa pluginu vrátane credentials, mappingov, retention a FTP. |

Skupiny vytvorí správca príkazom:

```shell
python manage.py config_backup_create_rbac_groups
```

Príkaz nepriradí žiadneho používateľa automaticky. Používateľov treba do skupín
zaradiť vedome. Po nastavení vždy otestuj nesuperuser účet a potvrď, že obsah a
download revisions vidia iba vybrané osoby.

## 20. Podporované drivery

Aktuálna inštalácia obsahuje najmä:

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
| Ceragon IP-20/IP-50 | natívny export prijatý backup receiverom |
| SIAE SM-OS | automatický driver; CLI snapshot alebo nakonfigurovaný natívny fallback |
| Fake | iba vývoj a automatické testy, nie produkcia |

Nie každý model a firmware výrobcu sa správa rovnako. Pred hromadným nasadením
otestuj jeden reprezentatívny kus každej platformy a verzie firmvéru.

### Natívne backup receivers

Niektoré zariadenia zálohu posielajú smerom k pluginu. Pre ne administrátor
nastaví **Settings → Security and vendor-specific setup → Device upload receivers**. Ide o inú
funkciu než FTP storage:

- native receiver prijíma súbor priamo zo zariadenia počas zberu,
- FTP storage kopíruje už dokončenú revision z pluginu na interný server.

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
| `HOST_KEY_UNKNOWN` | Over a schváľ zobrazený fingerprint. |
| `HOST_KEY_FAILED` / `HOST_KEY_MISMATCH` | Zariadenie mohlo vymeniť kľúč alebo ide o inú IP; neschvaľuj bez overenia. |
| `COMMAND_UNSUPPORTED` | Model alebo firmware nepodporuje príkaz drivera; potrebuje iný bezpečný workflow. |
| `INCOMPLETE_CONFIG` | Výstup neobsahuje úplnú konfiguráciu; skontroluj driver, oprávnenia a paging. |
| `NO_CREDENTIAL_PROFILE` | Mapping/target nemá kompletný credential profile. |
| `NO_RECEIVER_PROFILE` | Natívny driver nemá povolený receiver. |
| `DEVICE_EXPORT_FAILED` | Export bol odmietnutý alebo sa zariadenie nepripojilo k receiveru. |
| `DESTINATION_TEST_FAILED` | FTP login, práva na zápis/čítanie/mazanie, base path a passive firewall. |
| `INTERNAL_ERROR` | Otvor Background task a log web/worker procesu; môže ísť o chybu pluginu. |

Ak test prejde, ale backup zlyhá, porovnaj konkrétny BackupRun s testom. Natívny
backup môže trvať dlhšie, vytvárať súbor, používať reverse tunnel alebo vyžadovať
ďalšiu komunikáciu smerom k receiveru.

Ak run zostáva `Queued`, skontroluj dedikovaný worker a queue
`netbox_config_backup.backup`. FTP replication, audit, retention a recovery
používajú aj bežný NetBox worker.

## 22. Bezpečnostné zásady

- Plugin spúšťaj z oddelenej management siete.
- Používaj samostatné účty s minimálnymi právami.
- SSH host keys overuj podľa fingerprintu.
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
3. Priprav connection a credential profile.
4. Vytvor platform mapping.
5. Pridaj jeden testovací kus.
6. Over host key.
7. Spusti connection test.
8. Spusti manuálny backup.
9. Otvor revision, náhľad a artifact download.
10. Over FTP replica a integrity audit.
11. Až potom pridaj ďalšie zariadenia rovnakej platformy.

### Pravidelná kontrola

- denne sleduj Overview a nové failed/stale/stuck stavy,
- kontroluj FTP health a neúspešné replicas,
- pravidelne vyhodnocuj automatic integrity audit,
- pred zapnutím alebo zmenou retention použi preview,
- po upgrade otestuj aspoň jeden connection test, reálny backup, FTP copy a recovery ZIP,
- pravidelne over nesuperuser oprávnenia.

## 24. Odstránenie zariadenia z pluginu

Odstránenie backup targetu nevymaže NetBox zariadenie. Plugin pred potvrdením
zobrazí súvisiace runs, revisions a artifacty, ktoré budú zasiahnuté. Hromadné
odstránenie používa rovnakú kontrolu.

Pred odstránením over, či sú dôležité revisions chránené alebo bezpečne
exportované. FTP kópie sa automaticky nemažú.

## 25. Dokumenty pre administrátora

- [Inštalácia](docs/INSTALLATION.md)
- [Nasadenie a workery](docs/DEPLOYMENT.md)
- [Kompatibilita](COMPATIBILITY.md)
- [Bezpečnosť](SECURITY.md)
- [Rotácia master key](docs/MASTER_KEY_ROTATION.md)
- [FTP storage a recovery](docs/FTP_DESTINATION.md)

Po každej zmene verzie pluginu musí rovnakú verziu používať web proces, bežný
NetBox worker aj dedikovaný backup worker. Migrácie a `collectstatic` sa musia
vykonať pred reštartom služieb.
