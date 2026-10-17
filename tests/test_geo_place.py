# GDELT place checks: python3 -I -m unittest discover -s tests -p "test_*.py"
# pipeline/live/geo_place.py against stories GDELT put in the wrong place (and some it got right, which must stay).
# Needs GeoNames in raw/live_cache/geonames/ (fetch_live.py --only story downloads it); skipped without it.
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline", "live"))
import geo_place as gp  # noqa: E402

CACHE = os.path.join(ROOT, "raw", "live_cache")
HAVE = all(os.path.exists(os.path.join(CACHE, "geonames", f)) for f in gp.GN_FILES)

# GDELT name, FIPS country, lat, lon, article title, article summary, URL slug title, GDELT actors,
# where it should end up ((lat, lon), within 60 km, or 100 km for a region), or None to drop the story
CASES = [  # gname, fips, lat, lon, title, lead, slug, actors, expect (lat, lon) or None=drop
 ("Pokrovsk, Mordoviya, Russia","RS",54.77,45.84,None,None,"Russia Ukraine war drone strikes attacks civilians",["UKRAINE","RUSSIA"],(48.28,37.18)),
 ("Fasher, Kassala, Sudan","SU",15.07,35.95,None,None,"El Fasher bombing continues crisis in Sudans civil war",[],(13.63,25.35)),
 ("Sistan, Gilan, Iran","IR",36.96,49.85,"Iran faces growing Balochistan insurgency amid IRGC clashes","Reports of intensifying clashes between IRGC and Baloch rebels point to growing unrest in the country's southeast",None,[],(28.5,61)),
 ("Arakan, Yangon, Myanmar","BM",16.78,96.19,None,None,"airstrike near a market in Myanmars rakhine State kills people",[],(20.0,93.6)),
 ("Falah, H_efa, Israel","IS",32.71,34.94,None,None,"Israeli settlers attack Palestinians and Washington post photographer in west bank",[],(31.95,35.25)),
 ("Yakmach, Balochistan, Pakistan","PK",28.74,63.85,None,None,"Balochistan brg claims killing six Pakistani personnel wounding several others in sibi",[],(29.54,67.88)),
 ("Al-Sufal, Yemen","YM",14.11,48.72,None,None,"Houthi commander killed in clashes with Yemeni government forces in City of taiz official",[],(13.58,44.02)),
 ("Pakam, Wilayat Junqoley, South Sudan","OD",8.06,31.83,"SSPDF, Lakes officials dispute Rumbek violence death toll","South Sudan's army and local authorities in Lakes State have reported differing death tolls from violence in Rumbek on Friday",None,[],(6.81,29.68)),
 ("Voronezh, Russia","RS",51.36,42.08,"Ukraine Says Missile Strike Destroyed 6 Russian Warplanes at Voronezh Airfield","Ukraine's Navy said Thursday that it destroyed six Russian Su-34M fighter-bombers in an overnight missile strike on the Borisoglebsk military airfield in southwestern Russia's Voronezh region.",None,[],(51.37,42.10)),
 ("Volgograd, Volgogradskaya Oblast', Russia","RS",48.8,44.6,None,None,"Russian airstrikes choke key bridges in Kyiv",[],(50.45,30.52)),
 ("Tehran, Tehran, Iran","IR",35.75,51.51,"What's behind Iran's intensifying attacks on shipping in Hormuz?","With renewed tanker attacks, Tehran is betting that it can make passage through the Strait of Hormuz costly",None,[],(26.5,56.4)),
 ("Baidoa, Bay, Somalia","SO",3.11,43.65,"Somalia: Laftagareen-Aligned Forces Reportedly Seize Control of Burhakaba","Forces aligned with former South West State President Laftagareen reportedly seized control of the strategic town of Burhakaba on Thursday",None,[],(2.8,44.07)),
 ("Hangu, Tianjin, China","CH",39.25,117.79,None,None,"Pakistan says airstrikes kill militants",[],None),
 # must stay put
 ("Kyiv, Kyyiv, Misto, Ukraine","UP",50.43,30.52,"Zelensky says Russian strikes hit Ukraine's energy sites despite ceasefire push","The Ukrainian president said Moscow's forces pounded Ukraine's power grid",None,[],(50.43,30.52)),
 ("Tirana, Tirane, Albania","AL",41.33,19.82,"Riots erupt in Albania's capital as students join anti-government movement","TIRANA, Albania (AP) — Protesters in Albania attacked government buildings",None,[],(41.33,19.82)),
 ("Dhahran, Ash Sharqiyah, Saudi Arabia","SA",26.3,50.14,"ARAMCO HEADQUARTERS DEVASTATED: Houthi Attack Causes Extensive Damage to Saudi Oil Giant's Offices","Saudi Arabia's state-owned oil giant Aramco suffered extensive damage to its headquarters in Dhahran following a Houthi attack Tuesday night",None,[],(26.3,50.14)),
 ("Byala, Varna, Bulgaria","BU",42.88,27.88,"2 merchant ships attacked by drones near Bulgaria","SOFIA, Oct. 13 (Xinhua) -- Drones have attacked two merchant ships in the Black Sea off the coast of Bulgaria",None,[],(42.88,27.88)),
 ("Mogadishu, Banaadir, Somalia","SO",2.07,45.37,"Somalia's Mandate Crisis Deepens as Troops Surround Sharif Sheikh Ahmed's Residence","MOGADISHU (WDN) — Somalia's political crisis is entering another dangerous phase",None,[],(2.07,45.37)),
 ("Ryazan, Ryazanskaya Oblast', Russia","RS",54.63,39.69,"Ukraine resumes attacks on Russian oil refineries as tensions deepen with US","The strike could further complicate Kyiv's relations with US President Donald Trump",None,[],(54.63,39.69)),
 ("Mecca, Makkah, Saudi Arabia","SA",21.43,39.83,None,None,"Saudi led coalition says it destroys Houthi drone south of Mecca",[],(21.43,39.83)),
 ("Aden, Yemen","YM",12.78,45.01,None,None,"Houthi attack on Aden presidential palace kills five",[],(12.78,45.01)),
 ("Kantharalak, Sisaket, Thailand","TH",14.64,104.65,None,None,"Thailand says cambodian rocket fire has caused its first civilian death in new border fighting",[],(14.64,104.65)),
 ("Kidal, Kidal, Mali","ML",18.44,1.41,None,None,"Mali says its forces have retaken the Northern rebel stronghold of Kidal",[],(18.44,1.41)),
 ("Lahore, Pakistan","PK",31.56,74.35,None,None,"Jaish commander killed in Lahore along with wife and Son",[],(31.56,74.35)),
 ("Maiduguri, Borno, Nigeria","NI",11.85,13.16,None,None,"Nigeria suicide bombings leave dead injured",[],(11.85,13.16)),
 ("Tigray, Tigray, Ethiopia","ET",13.5,39.5,None,None,"Many children among dead in migrant shipwreck off Djibouti",["ETHIOPIA"],(11.59,43.15)),
 ("Manama, Al Manamah, Bahrain","BA",26.24,50.58,None,None,"Kuwait airport Saudi Arabia targeted Iran presses gulf attacks",[],(29.37,47.97)),
 ("Sofiya, Sofiya-Grad, Bulgaria","BU",42.68,23.32,None,None,"Bulgaria searches for missing crew after drones hit more Black sea ships",[],(43.22,27.91)),
 ("Vienna, Georgia, United States","US",32.09,-83.79,None,None,"Shots fired at Georgia block party during homecoming weekend leave dead hurt Vienna",[],(32.09,-83.79)),
 ("Baghdad, Baghdad, Iraq","IZ",33.34,44.39,None,None,"Iraqi shiite militia claims drone attack targeting us forces at airport in Iraqs capital",[],(33.34,44.39)),
 ("Gudumbali, Borno, Nigeria","NI",12.94,13.18,"Nigeria Says Airstrike Killed 40 Islamic State Fighters","The Nigerian air force said on Thursday that an airstrike in Borno state killed 40 fighters",None,[],(12.94,13.18)),
 ("Tel Aviv, Tel Aviv, Israel","IS",32.07,34.77,None,None,"United Arab Emirates briefly closes airspace as mideast fighting escalates",[],None),
 ("Kukawa, Yobe, Nigeria","NI",12.92,13.57,"Islamic State group-affiliated militants attack military base in Nigeria, killing several soldiers","ABUJA, Nigeria (AP) — Islamic State group-affiliated militants attacked a military base in Nigeria",None,[],(12.92,13.57)),
 ("Gilbert, Arizona, United States","US",33.35,-111.79,"Teen gets 30 years in killing of Gila River Officer Joshua Briese","A teenager was sentenced on Friday in Phoenix for the killing of a tribal police officer",None,[],(33.35,-111.79)),
 ("Porgera, Enga, Papua New Guinea","PP",-5.47,143.2,"Eight dead in September violence at Papua New Guinea's troubled Porgera","Details are emerging of violence which left up to eight people dead at Porgera Valley in Papua New Guinea's Enga Province",None,[],(-5.47,143.2)),
 ("Oreshnik, Kirovskaya Oblast', Russia","RS",57.36,49.97,"Russia 'uses new ballistic missile' in massive strike on Ukraine","Russia fired its new Oreshnik intermediate-range missile at the western city of Lviv overnight, Ukraine's air force said.",None,["RUSSIA","UKRAINE"],(49.84,24.03)),
 ("Pinotepa Nacional, Oaxaca, Mexico","MX",16.32,-98.02,"Gunmen kill journalist in Mexico, ninth in 2026","MEXICO CITY (AP) — Gunmen killed a journalist in the southern state of Oaxaca",None,[],(16.32,-98.02)),
 ("Kyiv, Kyyiv, Misto, Ukraine","UP",50.43,30.52,"Volodymyr Zelensky resumes attacks on Russian refineries after US withdraws intelligence","Ukraine struck two refineries overnight, officials said",None,[],(50.43,30.52)),
 ("Voronezh, Russia","RS",51.66,39.19,"Ukraine Says Missile Strike Destroyed 6 Russian Warplanes at Voronezh Airfield","Ukraine's Navy said Thursday that it destroyed six Russian Su-34M fighter-bombers in an overnight missile strike on the Borisoglebsk military airfield in southwestern Russia's Voronezh region.",None,[],(51.37,42.10)),
 ("Bureij, Syria","SY",34.26,36.77,"Two US service members, one civilian killed in ISIS ambush in Syria","The attack happened near Palmyra, Syria, on Saturday, US Central Command said",None,[],(34.56,38.28)),
 ("Al-Sufal, Yemen","YM",14.11,48.72,"Houthi commander killed in clashes with Yemeni government forces in city of Taiz: Officials","",None,[],(13.58,44.02)),
 ("Duqm, Al Wusta, Oman","MU",19.65,57.70,None,None,"The latest Irans supreme leader khamenei killed in us Israeli attack",["IRAN"],None),
]


@unittest.skipUnless(HAVE, "GeoNames not downloaded")
class Resolve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.P = gp.Places(CACHE, offline=True)

    def test_cases(self):
        for g, f, la, lo, t, d, sl, act, exp in CASES:
            with self.subTest(place=g):
                r = self.P.resolve(g, f, la, lo, title=t, lead=d, slug=sl, actors=act)
                if exp is None:
                    self.assertIsNone(r)
                else:
                    self.assertIsNotNone(r)
                    self.assertLessEqual(gp.hav(r["lat"], r["lon"], *exp), 100 if r["geo"] == "region" else 60, r)

    def test_words_are_not_places(self):
        found = [m[1] for m in self.P.mentions("officer shot near airport in august as police raid temple", False)]
        self.assertEqual(found, [])

    def test_menus_are_not_summaries(self):
        html = ("<html><head><title>12 killed in attack on Riyadh airport | Dunfermline Press</title></head><body>"
                "<p>Close Stay ahead with a digital subscription Subscribe Featured newsletters Morning Briefing Today's Most Read "
                "Crime and Court The Pars What's On Search News Dunfermline Rosyth Duloch Inverkeithing</p></body></html>")
        a = gp.parse_article(html)
        self.assertEqual((a["t"], a["d"]), ("12 killed in attack on Riyadh airport", ""))

    def test_article_meta(self):
        html = ('<html><head><meta property="og:title" content="Riots erupt in Tirana - AP News">'
                '<meta property="og:description" content="TIRANA, Albania (AP) &#8212; Protesters in Albania attacked government buildings and banks."></head></html>')
        a = gp.parse_article(html)
        self.assertEqual(a["t"], "Riots erupt in Tirana")
        self.assertTrue(a["d"].startswith("TIRANA, Albania (AP)"))


if __name__ == "__main__":
    unittest.main()
