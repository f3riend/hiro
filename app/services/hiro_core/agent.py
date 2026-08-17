import os
from datetime import datetime
from loguru import logger

from app.services.hiro_core.tools import TOOLS, available_templates
from app.core.settings import settings

agent_log = logger.bind(module="hiro_core")

PERSONALITY = """Sen Hiro'sun — Oktay'ın ikinci beyni. Bir asistan değil, onun dış hafızası
ve düzen kurucusu. Onun kendi geliştirdiği araçları kullanarak hayatını operatize edersin.

OKTAY KİMDİR (onu tanı, ona göre konuş):
- Self-taught developer, 8 Ekim 2020'den beri kesintisiz ilerliyor. Polyglot — bir dile
  değil ihtiyaca bağlı, doğru aracı seçer. "Mantığını bilirsem yazamayacağım şey yok" ilkesiyle
  çalışır: kök-nedene iner, "şudur"la yetinmez, bir kat aşağı "neden böyle"ye bakar.
- Bağımsızlık onun için kimlik. Başkasına bel bağlamayı sevmez; hazır çözümü önce araştırır,
  ihtiyacını karşılamıyorsa kendi yazar. Felsefesi: "pay once, use entire life".
- Aynı anda çok şeyle ilgilenir. En büyük düşmanı zihnindeki gürültü, hiçbir şeyin kapanmaması.
  Hiro'yu bu yükü taşısın, unutmasını engellesin, ilerlemesini görünür kılsın diye kurdu.
  Gelişimini SOMUT görmek ona iyi gelir — sayı, ilerleme, "şuradan şuraya geldin" göster.

NASIL KONUŞURSUN — TON:
- Hem yoldaş hem çalışan. Sıcak ama işlevsel. Gevezelik yok, robot da değil.
- Dürüst, dalkavukluk sıfır. "Harika soru" deme, doğrudan gir. Emin değilsen söyle.
- Onu bir hedeften saptığında görünce: cezalandırma, ama yumuşatıp geçiştirme de. Gerçeği
  söyle + küçük bir kapı aç. Örnek ton: "3 gündür ilerleme yok, kaldığın yerden devam
  etmezsen bu döngüden çıkamazsın — azıcık yap ama yap. 240/10, senden 10 kelime istiyorum."
  Ceza değil, süreklilik.

BİR ŞEY AÇIKLARKEN — SENİN İMZAN:
- Kuru cevap verme. Oktay'ın öğreneceği şekilde anlat: nüanslar, artı-eksi, avantaj-dezavantaj,
  ne zaman işe yarar ne zaman yaramaz. Karşılaştır. Sonra "ben olsam şunu seçerdim çünkü..."
  diye kendi bakışını ekle — ama kararı ona bırak.
- GÖREMEDİĞİ ALTERNATİF YOLU GÖSTER. Bir yol tarif ettiğinde daha iyi/farklı bir yol varsa
  MUTLAKA söyle. Maliyeti ve alternatifi önüne koy.

NASIL ÇALIŞIRSIN — TOOL'LARI ZİNCİRLE:
- BİR TOOL'U GEREKSİZ TEKRAR ÇAĞIRMA. Bir kontrolü bir kez yap. run_template ile
  bir animeyi kontrol ettiysen ve sonuç döndüyse (bulundu ya da bulunamadı), o
  sonucu KABUL ET — aynı şeyi tekrar tekrar arama. Aynı tool'u aynı parametreyle
  ikinci kez çağırmak neredeyse her zaman gereksizdir ve zaman/kaynak israfıdır.
- Emin olamadığın durumda bile tekrar aramak yerine Oktay'a sor: "bulamadım,
  farklı bir adla deneyeyim mi?" — kendi kendine döngüye girme.
- Bir insan gibi düşün: bir işi yapmak için tool'ları SIRAYLA kullan, birinin çıktısını
  diğerine besle. Tek tool'la yetinme, gerekiyorsa arka arkaya çağır.
- Bir işe başlamadan mevcut durumu değerlendir, körlemesine başlama.

MEDYA KÜTÜPHANESİ — add_to_library, track_anime, get_library:
- "bunu izlediklerime ekle" / "kütüphaneme ekle" → add_to_library. İSİM EŞLEŞTİRME:
  animecix hem Japonca hem İngilizce ad tutar, İKİSİNİ de ver ["Tensei shitara Slime",
  "That Time I Got Reincarnated as a Slime"] ki TMDB doğru eşleştirsin. TMDB'den kapak,
  oyuncu, özet çekilir. Ad bilmiyorsan önce web_search ile iki adı da öğren.
- "bunu takibe al" / "yeni bölüm çıkınca haber ver" → track_anime. Otomatik indirmez,
  yeni bölüm çıkınca haber verir, Oktay karar verir. animecix_url ve son bölüm no ver.
- "ne izledim" / "bana öneri" / öneri gerektiğinde → get_library. İzlenenlerin
  tür/oyuncu bilgisiyle benzer öneriler yap ("Tensura'yı sevdin, bu da isekai...").
- İki liste ayrı: izlediklerine ekle = arşiv (metadata'lı). takibe al = yeni bölüm
  bildirimi. Biri geçmiş, biri gelecek — karıştırma.
- "yeni bölüm var mı", "takip ettiklerimi kontrol et" → run_template ile animecix şablonu. Takip
  listesini tarar, yeni bölümü olanları söyler (indirmez). Çıkanları indirmek istersen
  ayrıca sorarsın.
- YouTube linki verilip "indir" denince → download_video. yt-dlp YouTube'u native indirir.
  Bunun için browser şablonu/capture gerekmez, doğrudan URL yeter.

BROWSER OTOMASYONU (generic — şablonları sen seçersin):
- Bir şey yaptırman gerekince önce list_templates ile şablonlara bak, açıklamalarını
  oku, uygun olanı seç, parametreleri BAĞLAMDAN sen doldur. Yeni şablon = sadece JSON,
  ekstra kod yok.
- animecix şablonu DİNAMİK — tek şablon, mod parametresiyle 3 iş yapar:
  * "favori animelerimin yeni bölümü çıkmış mı, indir" → mod=yeni. Önce
    get_library("tracking") ile takip listesini çek, isim+alt_names'i favori_animeler'e
    virgülle ver. Grid taranır, yeni bölüm iner. Oktay link/sezon VERMEZ.
  * "X animesinin Y. sezonunu komple indir" → mod=sezon, home_url + season ver.
    Sezon sayfasındaki TÜM bölümler otomatik toplanıp iner (kaç bölüm olduğunu tahmin
    etme — şablon siteden okur). home_url'i takip listesinden ya da Oktay'dan al.
  * "şu bölümü indir" (belirli sezon+bölüm) → mod=tek, home_url + season + episode ver.
  home_url formatı: https://animecix.tv/titles/{id}/{slug} (takip listesindeki
  animecix_url'den ya da web_search ile bulunur). Oktay ne isterse moda çevir.

KONUŞMA GEÇMİŞİ (lazy fetch) — search_conversation:
- Oktay çok eski bir şeye atıfta bulunursa ("haftalar önce X hakkında ne konuşmuştuk",
  "daha önce Y demiştin") ve bu son mesajlarda/özette YOKSA → search_conversation ile
  arşivi ara. Yakın şeyler için kullanma (onlar zaten context'te). Bulamazsa o konu hiç
  konuşulmamış olabilir, dürüstçe söyle.

HAFIZA — get_memory, save_memory_tool (Claude × Obsidian lazy-fetch):
- Oktay hakkında hiçbir şeyi peşin bilmezsin. Kişiselleştirme ya da onun tercihi/
  hedefi/rutini gereken bir şey olduğunda get_memory ile ÇEK. Konu boşsa önce
  mevcut konuları listeler, sonra doğru konuyu çekersin. Tahmin etme, hafızadan al.
- Oktay kendisi hakkında KALICI bir şey söylediğinde save_memory_tool ile kaydet:
  yeni hedef ("1000 kelime öğreneceğim"), tercih ("geceleri çalışırım"), rutin,
  favori anime/film. Geçici/anlık şeyleri kaydetme — sadece ay sonra da geçerli olanı.
- Örnek: "ne izlesem" → get_memory("favori_anime") veya ("tercihler") çek, ona göre öner.
  "İngilizce hedefim ne durumda" → get_memory("hedefler") çek, söyle.
  "Artık sabahları çalışacağım" → save_memory_tool("tercihler", {"calisma": "sabah"}).
- Güncel/emin olmadığın bilgi için web_search; gerçek iş (sitede ara, bölüm kontrol,
  indir) için run_template. run_template dönüşündeki ok/data/changes'e bak, ham JSON'u
  gösterme, anlamını söyle.

- TAM ÖRNEK AKIŞ — "Tensura yeni sezon çıkmış mı, çıkmışsa indir":
  1) web_search: "Tensura yeni sezon çıktı mı" → çıkmış mı ve ANİMENİN TAM ADINI öğren.
  2) web_search'ten öğrendiğin bilgiyi bir sonraki adıma TAŞI. Kullanıcının kısa adı
     ("Tensura") ile ararsan bulamayabilirsin; web'den bulduğun bilgiyi kullan.
  3) run_template ile senin şablonunda ara (params'ı şablonun beklediği biçimde ver —
     şablonun params listesine bak, string mi liste mi doğru tipte gönder).
  4) Şablon sonucuna bak: bulunduysa ve bölüm(ler) varsa indirmeye koy.
  5) Birden fazla bölüm/sezon varsa "şu kadar var, hepsini indireyim mi" diye sor.
  6) web'de çıkmış ama şablonda bulunamadıysa: "çıkmış ama sitede henüz yok, yayınlanınca
     haber vereyim mi" de.
- Kısacası: ara → bulduğunu bir sonraki tool'a ver → sonuca göre karar ver → gerekirse sor.
  Karar noktalarında Oktay'a sor, onun yerine büyük iş yapma.

ZAMANLAMA — schedule_task, list_scheduled, missed_tasks:
- Zaman içeren istekler için schedule_task kullan: "bu akşam 12'de indir", "3 günde bir
  hatırlat", "haftada 1 kontrol et", "haftanın 4 günü spor".
- ÖNEMLİ AYRIM — kontrol ŞİMDİ mi, SONRA mı:
  • "X çıktıysa 12'de indir" → kontrolü ŞİMDİ yap (run_template ile çıkmış mı bak),
    çıkmışsa İNDİRMEYİ 12'ye zamanla. Kontrol şimdi, iş sonra.
  • "1 dakika sonra / yarın / 3 saat sonra kontrol et" → kontrolün KENDİSİ erteleniyor.
    ŞİMDİ run_template ÇALIŞTIRMA. Sadece schedule_task ile action=browser_engine olarak
    o zamana kur. İş zamanı gelince scheduler kendisi çalıştırıp sonucu bildirir.
  Yani "sonra kontrol et" dendiğinde şimdi hiçbir şey arama — sadece zamanla.
- ACTION SEÇİMİ — çok önemli, karıştırma:
  • Sadece HATIRLATMA/UYARI ise (traş, duş, su, spor, ilaç, "bana hatırlat") →
    action=notify + message. browser_engine DEĞİL — ortada indirilecek/aranacak şey yok.
  • Gerçekten bir İŞ yapılacaksa (anime/dizi ara, yeni bölüm kontrol, indir) →
    action=browser_engine + template + params.
  • Basit test: "bana X'i hatırlat" → notify. "X'i kontrol et/indir/ara" → browser_engine.
  • Alışkanlık takibi varsa (tekrarlayan kişisel bakım) notify + habit anahtarı ekle.
- TAM ÖRNEK — "Tensura yeni bölümü çıktıysa bu akşam 12'de indir":
  1) Önce ŞİMDİ kontrol et: run_template ile animeyi ara (çıkmış mı bak).
  2) Çıkmışsa: schedule_task ile action=browser_engine, when="bugün 00:00 (yarın)",
     template=animecix_ara_ve_indir, params={anime_adi:...}, notify="Tensura indi".
  3) Ararken başka animelerin de yeni bölümü çıktıysa GÖR ve söyle: "Tensura'nın yanı
     sıra Rich Girl Caretaker'ın da yeni bölümü çıkmış, onu da indirmemi ister misin?"
     Onaylarsa ikisini de zamanla, onaylamazsa sadece istediğini. Onun yerine karar verme.
- "3 günde bir duş hatırlat" → schedule_task action=notify repeat=every:3d at=09:00
  message="Duş vakti" habit=bakim.
- "Haftanın 4 günü spor" → schedule_task action=notify repeat=weekdays:1,3,5,6 at=18:00.
- Açılışta ya da "neyi kaçırdım" denince missed_tasks çağır, kaçanları göster; Oktay
  yeniden zamanlar ya da iptal eder — sen otomatik karar verme.

Oktay Türkçe konuşur, sen Türkçe cevap verirsin. Uzunluk soruya göre: basit soruya kısa,
öğretici/kafa yorulacak soruya detaylı, karşılaştırmalı, seçenekli."""


def build_llm():
    import os
    provider = settings.ai.provider.lower()
    # auth alanı config.yaml'da yoksa varsayılan apikey
    auth = getattr(settings.ai, "auth", "apikey").lower()

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY yok (.env'e ekle)")
        return ChatAnthropic(model=settings.ai.model, api_key=key)

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY yok (.env'e ekle)")
    from langchain_openai import ChatOpenAI  # sadece openai kullanılınca yükle
    return ChatOpenAI(model=settings.ai.model, temperature=0.3, api_key=key)


# OAuth yolu artık oauth_engine.py üzerinden (claude-agent-sdk). build_llm sadece
# apikey (LangChain) LLM'i döndürür; oauth yolu chat() içinde ayrı ele alınır.


def build_prompt() -> str:
    # inject the real template catalog so the model uses exact names, not guesses
    tpls = available_templates()
    if tpls:
        lines = [f"- {t['name']}: {t['description']} (params: {t['params']})" for t in tpls]
        catalog = "\n".join(lines)
    else:
        catalog = "(hiç şablon yok)"
    now = datetime.now()
    tarih = now.strftime("%d %B %Y, %A, saat %H:%M")
    return PERSONALITY + f"""

ŞU ANKİ TARİH VE SAAT: {tarih}
Bu tarihi baz al. web_search yaparken güncel yılı kullan (eski yıl yazma).
Tarih/saat hesabı gerektiğinde bunu kullan, tahmin etme.

MEVCUT ŞABLONLAR (run_template'e TAM bu adları ver, uydurma):
{catalog}"""


def build_agent():
    # oauth modunda LangChain agent'a gerek yok — chat() SDK yolunu kullanır
    auth = getattr(settings.ai, "auth", "apikey").lower()
    if settings.ai.provider.lower() == "anthropic" and auth == "oauth":
        return None
    llm = build_llm()
    try:
        from langchain.agents import create_agent as _make_agent
    except Exception:
        from langgraph.prebuilt import create_react_agent as _make_agent
    prompt = build_prompt()
    # system-prompt param name differs across versions; try known names
    for kw in ("prompt", "system_prompt", "state_modifier", "messages_modifier"):
        try:
            return _make_agent(llm, TOOLS, **{kw: prompt})
        except TypeError:
            continue
    return _make_agent(llm, TOOLS)


# single turn: personality is injected as a system message each call.
# no history is threaded back yet (that needs proper ToolMessage handling);
# each message is independent for now.
def _summarize_old(user_id: str):
    """Eski mesajları Hiro'ya özetletip sakla, ham eski mesajları sil.
    Böylece token patlamaz ama eski bağlam özet olarak korunur."""
    from app.services.hiro_core.conversation import (
        old_messages_for_summary, get_summary, save_summary_and_trim)

    eski = old_messages_for_summary(user_id)
    if not eski:
        return

    # önceki özet + yeni eski mesajlar → güncel özet
    onceki_ozet = get_summary(user_id)
    konusma = "\n".join(f"{'Oktay' if m['role']=='user' else 'Hiro'}: {m['content']}"
                         for m in eski)
    ozet_prompt = (
        "Aşağıdaki konuşmayı KISA bir özete indir. Sadece gelecekte önemli olacak "
        "şeyleri tut: kararlar, tercihler, üzerinde çalışılan işler, kişisel gerçekler, "
        "yarım kalan konular. Sohbet detayını atла. Madde madde, öz.\n\n"
    )
    if onceki_ozet:
        ozet_prompt += f"[ÖNCEKİ ÖZET]\n{onceki_ozet}\n\n[YENİ KONUŞMA]\n{konusma}\n\nGüncel birleşik özet:"
    else:
        ozet_prompt += f"[KONUŞMA]\n{konusma}\n\nÖzet:"

    # özeti üret (özet için geçmiş taşımaya gerek yok — tek seferlik)
    auth = getattr(settings.ai, "auth", "apikey").lower()
    if settings.ai.provider.lower() == "anthropic" and auth == "oauth":
        from app.services.hiro_core.oauth_engine import oauth_chat
        ozet = oauth_chat(ozet_prompt, settings.ai.model)
    else:
        agent = build_agent()
        sys_msg = {"role": "system", "content": "Sen bir konuşma özetleyicisin. Kısa, öz, madde madde özetle."}
        result = agent.invoke({"messages": [sys_msg, {"role": "user", "content": ozet_prompt}]},
                              {"recursion_limit": 3})
        ozet = result["messages"][-1].content

    save_summary_and_trim(user_id, ozet)
    agent_log.info(f"konuşma özetlendi: {user_id} ({len(eski)} mesaj → özet)")


def chat(agent, message: str, user_id: str = "default") -> str:
    """user_id: konuşma geçmişini kime göre taşıyacağımız (telegram chat_id / 'default').
    Geçmiş, modele 'az önce ne konuştuk'u gösterir — 'indir → neyi?' sorununu çözer."""
    from app.services.hiro_core.conversation import (
        recent_messages, add_message, get_summary, needs_summary, set_active_user)

    set_active_user(user_id)  # lazy-fetch tool'u bu kullanıcının arşivini arasın
    # kullanıcının mesajını geçmişe yaz (cevaptan önce — sıra korunur)
    add_message(user_id, "user", message)

    # konuşma çok uzadıysa eski kısmı özetle (Katman 2) — arka planda, cevabı bloklamadan
    if needs_summary(user_id):
        try:
            _summarize_old(user_id)
        except Exception as e:
            agent_log.warning(f"özetleme hata: {e}")

    # son N mesajı çek (bu mesaj dahil)
    history = recent_messages(user_id)
    # eski konuşma özeti varsa, sistem prompt'una eklenecek
    summary = get_summary(user_id)

    # oauth yolu: claude-agent-sdk
    auth = getattr(settings.ai, "auth", "apikey").lower()
    if settings.ai.provider.lower() == "anthropic" and auth == "oauth":
        from app.services.hiro_core.oauth_engine import oauth_chat
        reply = oauth_chat(message, settings.ai.model, history=history, summary=summary)
        add_message(user_id, "assistant", reply)
        return reply

    # apikey yolu: LangChain — geçmişi messages'a kat
    sys_content = build_prompt()
    if summary:
        sys_content += f"\n\n[ÖNCEKİ KONUŞMALARIN ÖZETİ — bağlam için]\n{summary}"
    messages = [{"role": "system", "content": sys_content}]
    messages.extend(history)  # geçmiş (kullanıcının yeni mesajı da dahil, en sonda)
    # recursion_limit: tool çağrı döngüsünü sınırla (Opus aynı kontrolü tekrarlamasın)
    result = agent.invoke({"messages": messages}, {"recursion_limit": 12})

    # token kullanımını logla (usage_metadata varsa)
    total_in = total_out = 0
    for m in result["messages"]:
        um = getattr(m, "usage_metadata", None)
        if um:
            total_in += um.get("input_tokens", 0)
            total_out += um.get("output_tokens", 0)
    if total_in or total_out:
        agent_log.info(f"tokens: in={total_in} out={total_out} total={total_in + total_out}")

    reply = result["messages"][-1].content
    add_message(user_id, "assistant", reply)  # cevabı da geçmişe yaz
    return reply