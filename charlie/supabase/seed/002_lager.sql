-- Inventory seed: Norway (main workshop, ~300 m2) and Switzerland (~160 m2).
-- Shelves R1-R11 (3 levels each) in both locations + an ESD zone (ESD-1..ESD-4).
-- Idempotent: re-running the seed changes nothing.

insert into lager.lokasjon (id, navn, land, valuta) values
  ('NO', 'Verksted Norge', 'Norge', 'NOK'),
  ('CH', 'Verksted Sveits', 'Sveits', 'CHF')
on conflict (id) do nothing;

insert into lager.lagerplass (lokasjon_id, kode, reol, hylle, esd)
select l.id, 'R' || r || '-H' || h, 'R' || r, h, false
  from lager.lokasjon l, generate_series(1, 11) r, generate_series(1, 3) h
on conflict (lokasjon_id, kode) do nothing;

insert into lager.lagerplass (lokasjon_id, kode, reol, hylle, esd)
select l.id, 'ESD-' || h, 'ESD', h, true
  from lager.lokasjon l, generate_series(1, 4) h
on conflict (lokasjon_id, kode) do nothing;

insert into lager.leverandor (id, navn, land, epost, nettside, leveringstid_dager, valuta) values
  ('elfa',       'Elfa Distrelec',  'Norge',   'ordre@elfa.example',      'https://www.elfadistrelec.no', 2, 'NOK'),
  ('robotshop',  'RobotShop',       'Canada',  'orders@robotshop.example', 'https://www.robotshop.com',   7, 'NOK'),
  ('biltema',    'Biltema',         'Norge',   null,                       'https://www.biltema.no',      1, 'NOK'),
  ('igus',       'igus',            'Tyskland','order@igus.example',       'https://www.igus.eu',         5, 'NOK'),
  ('misumi',     'MISUMI Europa',   'Tyskland','order@misumi.example',     'https://de.misumi-ec.com',    6, 'NOK'),
  ('conrad-ch',  'Conrad Schweiz',  'Sveits',  'order@conrad.example',     'https://www.conrad.ch',       2, 'CHF'),
  ('distrelec-ch','Distrelec Schweiz','Sveits','order@distrelec.example',  'https://www.distrelec.ch',    2, 'CHF')
on conflict (id) do nothing;

insert into lager.artikkel (sku, navn, kategori, enhet, esd_sensitiv, pris_nok, leverandor_id, leverandor_sku) values
  ('MOT-NEMA17',    'Steppermotor NEMA 17, 0.45 Nm',       'Motorer',        'stk', false, 189.00, 'robotshop', 'RB-SOY-01'),
  ('MOT-NEMA23',    'Steppermotor NEMA 23, 1.9 Nm',        'Motorer',        'stk', false, 459.00, 'robotshop', 'RB-SOY-23'),
  ('MOT-BLDC-5010', 'BLDC-motor 5010 360KV',               'Motorer',        'stk', false, 549.00, 'robotshop', 'RB-T-5010'),
  ('SRV-MG996R',    'Servo MG996R',                        'Motorer',        'stk', false,  99.00, 'conrad-ch', 'C-MG996R'),
  ('SRV-DXL-XL430', 'Dynamixel XL430-W250',                'Motorer',        'stk', false, 699.00, 'robotshop', 'RB-DYN-430'),
  ('DRV-TMC2209',   'Motordriver TMC2209',                 'Elektronikk',    'stk', true,   79.00, 'elfa',      'E-TMC2209'),
  ('DRV-ODRIVE-S1', 'ODrive S1 motorkontroller',           'Elektronikk',    'stk', true, 1590.00, 'robotshop', 'RB-ODR-S1'),
  ('MCU-ESP32S3',   'ESP32-S3 DevKitC',                    'Elektronikk',    'stk', true,  149.00, 'elfa',      'E-ESP32S3'),
  ('MCU-RP2040',    'Raspberry Pi Pico',                   'Elektronikk',    'stk', true,   69.00, 'elfa',      'E-RP2040'),
  ('MCU-STM32F4',   'STM32F411 Black Pill',                'Elektronikk',    'stk', true,  119.00, 'elfa',      'E-STM32F4'),
  ('SBC-RPI5-8G',   'Raspberry Pi 5, 8 GB',                'Elektronikk',    'stk', true, 1099.00, 'elfa',      'E-RPI5-8'),
  ('SBC-JETSON-ON', 'Jetson Orin Nano devkit',             'Elektronikk',    'stk', true, 3990.00, 'distrelec-ch', 'D-JON'),
  ('SEN-IMU-BNO085','IMU BNO085',                          'Sensorer',       'stk', true,  289.00, 'elfa',      'E-BNO085'),
  ('SEN-LIDAR-LD19','LiDAR LD19',                          'Sensorer',       'stk', false, 899.00, 'robotshop', 'RB-LD19'),
  ('SEN-TOF-VL53',  'ToF-sensor VL53L1X',                  'Sensorer',       'stk', true,  129.00, 'elfa',      'E-VL53L1X'),
  ('SEN-ENC-AS5600','Magnetisk encoder AS5600',            'Sensorer',       'stk', true,   59.00, 'elfa',      'E-AS5600'),
  ('SEN-CAM-OV5647','Kameramodul OV5647',                  'Sensorer',       'stk', true,  199.00, 'elfa',      'E-OV5647'),
  ('PWR-PSU-24V10', 'Strømforsyning 24 V 10 A',            'Strøm',          'stk', false, 549.00, 'elfa',      'E-PSU2410'),
  ('PWR-BUCK-5V5',  'Buck-omformer 5 V 5 A',               'Strøm',          'stk', true,   89.00, 'elfa',      'E-BUCK55'),
  ('BAT-LIPO-4S',   'LiPo 4S 5000 mAh',                    'Batterier',      'stk', false, 899.00, 'robotshop', 'RB-LIPO4S'),
  ('BAT-LIION-18650','Li-ion-celle 18650 3500 mAh',        'Batterier',      'stk', false,  79.00, 'elfa',      'E-18650'),
  ('BAT-LIFEPO4-12V','LiFePO4 12 V 20 Ah',                 'Batterier',      'stk', false, 1890.00, 'biltema',  'B-LFP1220'),
  ('MEK-PROF-2020', 'Aluprofil 20x20, 1 m',                'Mekanikk',       'stk', false,  89.00, 'misumi',    'M-HFS5-2020'),
  ('MEK-PROF-4040', 'Aluprofil 40x40, 1 m',                'Mekanikk',       'stk', false, 239.00, 'misumi',    'M-HFS8-4040'),
  ('MEK-LAGER-608', 'Kulelager 608ZZ',                     'Mekanikk',       'stk', false,   9.00, 'misumi',    'M-608ZZ'),
  ('MEK-SKINNE-MGN12','Lineærskinne MGN12, 400 mm',        'Mekanikk',       'stk', false, 349.00, 'igus',      'I-MGN12-400'),
  ('MEK-REIM-GT2',  'GT2-reim 6 mm, 5 m',                  'Mekanikk',       'stk', false, 129.00, 'robotshop', 'RB-GT2-5'),
  ('MEK-TANNHJ-GT2','GT2-tannhjul 20T',                    'Mekanikk',       'stk', false,  39.00, 'robotshop', 'RB-GT2-20T'),
  ('MEK-KOBL-5-8',  'Akselkobling 5-8 mm',                 'Mekanikk',       'stk', false,  49.00, 'misumi',    'M-CPL58'),
  ('FEST-M3-SET',   'Skruesett M3, rustfritt (500 stk)',   'Festemidler',    'pk',  false, 199.00, 'biltema',   'B-M3-500'),
  ('FEST-M5-TNUT',  'T-mutter M5 for 2020 (100 stk)',      'Festemidler',    'pk',  false, 149.00, 'misumi',    'M-TNUT5'),
  ('FEST-INSERT-M3','Gjengeinnsats M3 (100 stk)',          'Festemidler',    'pk',  false, 119.00, 'robotshop', 'RB-INS-M3'),
  ('PRN-PLA-1KG',   'PLA-filament 1 kg, svart',            'Forbruk',        'rull',false, 229.00, 'biltema',   'B-PLA1'),
  ('PRN-PETG-1KG',  'PETG-filament 1 kg, grå',             'Forbruk',        'rull',false, 259.00, 'biltema',   'B-PETG1'),
  ('PRN-TPU-500',   'TPU-filament 500 g',                  'Forbruk',        'rull',false, 289.00, 'robotshop', 'RB-TPU05'),
  ('LOD-TINN-07',   'Loddetinn 0,7 mm, blyfri',            'Forbruk',        'rull',false, 249.00, 'elfa',      'E-SN07'),
  ('LOD-FLUSS',     'Flusspenn',                           'Forbruk',        'stk', false,  89.00, 'elfa',      'E-FLUX'),
  ('KAB-JST-SET',   'JST-XH kontaktsett',                  'Kabler',         'pk',  false, 159.00, 'elfa',      'E-JSTXH'),
  ('KAB-SILIKON-22','Silikonkabel 22 AWG, 10 m',           'Kabler',         'rull',false,  99.00, 'elfa',      'E-SIL22'),
  ('KAB-CAN-TERM',  'CAN-buss terminering 120 ohm',        'Kabler',         'stk', true,   35.00, 'elfa',      'E-CAN120'),
  ('VERN-BRILLER',  'Vernebriller, klar',                  'Verneutstyr',    'stk', false,  59.00, 'biltema',   'B-VB01'),
  ('VERN-HANSKER',  'Nitrilhansker (100 stk)',             'Verneutstyr',    'pk',  false, 129.00, 'biltema',   'B-NIT100'),
  ('ESD-ARMBAND',   'ESD-armbånd',                         'Verneutstyr',    'stk', false,  79.00, 'elfa',      'E-ESDWB'),
  ('SRV-CH-SG90',   'Mikroservo SG90',                     'Motorer',        'stk', false,  29.00, 'conrad-ch', 'C-SG90'),
  ('MCU-CH-ARDNANO','Arduino Nano Every',                  'Elektronikk',    'stk', true,  149.00, 'distrelec-ch', 'D-NANOEV')
on conflict (sku) do nothing;

-- Stock rules (min/max) per location. Each location orders independently.
insert into lager.lagerregel (artikkel_id, lokasjon_id, min_antall, maks_antall)
select a.id, r.lok, r.mn, r.mx from (values
  ('MOT-NEMA17','NO',6,20), ('MOT-NEMA23','NO',2,6), ('MOT-BLDC-5010','NO',2,6),
  ('SRV-DXL-XL430','NO',2,8), ('DRV-TMC2209','NO',10,30), ('DRV-ODRIVE-S1','NO',1,3),
  ('MCU-ESP32S3','NO',5,15), ('MCU-RP2040','NO',5,20), ('MCU-STM32F4','NO',3,10),
  ('SBC-RPI5-8G','NO',1,3), ('SEN-IMU-BNO085','NO',2,6), ('SEN-LIDAR-LD19','NO',1,2),
  ('SEN-TOF-VL53','NO',4,12), ('SEN-ENC-AS5600','NO',6,20), ('PWR-PSU-24V10','NO',1,3),
  ('PWR-BUCK-5V5','NO',4,10), ('BAT-LIPO-4S','NO',4,8), ('BAT-LIION-18650','NO',8,24),
  ('MEK-PROF-2020','NO',10,40), ('MEK-LAGER-608','NO',20,100), ('MEK-REIM-GT2','NO',2,6),
  ('FEST-M3-SET','NO',1,3), ('PRN-PLA-1KG','NO',3,8), ('PRN-PETG-1KG','NO',2,6),
  ('LOD-TINN-07','NO',1,3), ('VERN-BRILLER','NO',10,20), ('VERN-HANSKER','NO',2,5),
  ('ESD-ARMBAND','NO',3,8),
  ('SRV-MG996R','CH',6,12), ('SRV-CH-SG90','CH',10,30), ('MCU-CH-ARDNANO','CH',3,8),
  ('MCU-ESP32S3','CH',3,8), ('SBC-JETSON-ON','CH',1,2), ('SEN-CAM-OV5647','CH',2,6),
  ('MEK-PROF-2020','CH',6,20), ('FEST-M5-TNUT','CH',2,5), ('PRN-PLA-1KG','CH',2,5),
  ('LOD-TINN-07','CH',1,2), ('VERN-BRILLER','CH',6,12), ('BAT-LIION-18650','CH',4,12)
) as r(sku, lok, mn, mx)
join lager.artikkel a on a.sku = r.sku
on conflict (artikkel_id, lokasjon_id) do nothing;

-- Initial stock as 'mottak' movements (only if there is no movement history yet).
do $$
begin
  if not exists (select 1 from lager.bevegelse) then
    insert into lager.bevegelse (artikkel_id, lagerplass_id, endring, arsak, utfort_av, kommentar)
    select a.id, p.id, s.antall, 'mottak', 'seed', 'startbeholdning'
      from (values
        ('MOT-NEMA17','NO','R3-H2',12), ('MOT-NEMA23','NO','R3-H3',1), ('MOT-BLDC-5010','NO','R3-H1',4),
        ('SRV-DXL-XL430','NO','R4-H1',3), ('DRV-TMC2209','NO','ESD-1',3), ('DRV-ODRIVE-S1','NO','ESD-1',1),
        ('MCU-ESP32S3','NO','ESD-2',8), ('MCU-RP2040','NO','ESD-2',14), ('MCU-STM32F4','NO','ESD-2',2),
        ('SBC-RPI5-8G','NO','ESD-3',2), ('SEN-IMU-BNO085','NO','ESD-3',1), ('SEN-LIDAR-LD19','NO','R5-H2',1),
        ('SEN-TOF-VL53','NO','ESD-4',6), ('SEN-ENC-AS5600','NO','ESD-4',9), ('PWR-PSU-24V10','NO','R6-H1',2),
        ('PWR-BUCK-5V5','NO','ESD-4',5), ('BAT-LIPO-4S','NO','R11-H1',2), ('BAT-LIION-18650','NO','R11-H2',16),
        ('BAT-LIFEPO4-12V','NO','R11-H3',1), ('MEK-PROF-2020','NO','R1-H1',22), ('MEK-PROF-4040','NO','R1-H2',6),
        ('MEK-LAGER-608','NO','R2-H1',60), ('MEK-SKINNE-MGN12','NO','R2-H2',4), ('MEK-REIM-GT2','NO','R2-H3',1),
        ('MEK-TANNHJ-GT2','NO','R2-H3',12), ('MEK-KOBL-5-8','NO','R2-H3',8), ('FEST-M3-SET','NO','R7-H1',2),
        ('FEST-M5-TNUT','NO','R7-H1',3), ('FEST-INSERT-M3','NO','R7-H2',2), ('PRN-PLA-1KG','NO','R8-H1',4),
        ('PRN-PETG-1KG','NO','R8-H2',1), ('PRN-TPU-500','NO','R8-H3',2), ('LOD-TINN-07','NO','R9-H1',2),
        ('LOD-FLUSS','NO','R9-H1',3), ('KAB-JST-SET','NO','R10-H1',2), ('KAB-SILIKON-22','NO','R10-H2',3),
        ('KAB-CAN-TERM','NO','ESD-4',6), ('VERN-BRILLER','NO','R9-H3',14), ('VERN-HANSKER','NO','R9-H3',1),
        ('ESD-ARMBAND','NO','R9-H2',5),
        ('SRV-MG996R','CH','R2-H1',4), ('SRV-CH-SG90','CH','R2-H2',25), ('MCU-CH-ARDNANO','CH','ESD-1',5),
        ('MCU-ESP32S3','CH','ESD-1',2), ('SBC-JETSON-ON','CH','ESD-2',1), ('SEN-CAM-OV5647','CH','ESD-2',3),
        ('MEK-PROF-2020','CH','R1-H1',8), ('FEST-M5-TNUT','CH','R7-H1',1), ('PRN-PLA-1KG','CH','R8-H1',3),
        ('LOD-TINN-07','CH','R9-H1',1), ('VERN-BRILLER','CH','R9-H3',8), ('BAT-LIION-18650','CH','R11-H1',6)
      ) as s(sku, lok, plass, antall)
      join lager.artikkel a on a.sku = s.sku
      join lager.lagerplass p on p.lokasjon_id = s.lok and p.kode = s.plass;
  end if;
end $$;

insert into lager.batteri (serienr, artikkel_id, lagerplass_id, kjemi, celler, kapasitet_mah, sykluser, sist_ladet, lagringsspenning, status)
select b.serienr, a.id, p.id, b.kjemi, b.celler, b.kap, b.sykl, b.ladet::date, b.lagring, b.status
  from (values
    ('LIPO-NO-001','BAT-LIPO-4S','NO','R11-H1','LiPo',4,5000,42,'2026-09-28',true,'lagring'),
    ('LIPO-NO-002','BAT-LIPO-4S','NO','R11-H1','LiPo',4,5000,118,'2026-08-14',true,'ok'),
    ('LFP-NO-001','BAT-LIFEPO4-12V','NO','R11-H3','LiFePO4',4,20000,15,'2026-10-01',true,'ok'),
    ('PACK-NO-007',null,'NO','R11-H2','Li-ion',6,7000,301,'2026-06-02',false,'resirkuler'),
    ('PACK-CH-001',null,'CH','R11-H1','Li-ion',3,3500,12,'2026-09-30',true,'lagring')
  ) as b(serienr, sku, lok, plass, kjemi, celler, kap, sykl, ladet, lagring, status)
  left join lager.artikkel a on a.sku = b.sku
  join lager.lagerplass p on p.lokasjon_id = b.lok and p.kode = b.plass
on conflict (serienr) do nothing;
