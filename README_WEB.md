# Mesai Web Portal

Bu klasör, Android uygulamasına paralel çalışan web portalıdır.

## Özellikler
- Kayıt ol
- Giriş yap
- Şifremi unuttum (SMTP ile gerçek e-posta gönderimi)
- Kullanıcıya özel mesai kayıt ekranı (otomatik hesaplama dahil)
- Kayıt ekleme / silme
- Raporlar: dönem seçimi, yıl seçimi, dönem/yıl toplamları
- Excel aktarma + CSV dışa aktarma
- `APK Uygulama İndir` butonu
- Mobil uygulama senkronu için token bazlı API (`/api/*`)

## En Kolay Kurulum (Tek tek bilmen gerekmiyor)

1. `WEB_PORTAL_ILK_KURULUM.bat` dosyasını çift tıkla  
   - Site adresi, SMTP mail bilgileri vb soruları doldur.
2. `START_WEB_PORTAL.bat` dosyasını çift tıkla  
   - Web portal çalışır.
3. Tarayıcıdan aç: `http://127.0.0.1:5000`
4. Kayıt ol ekranından ilk kullanıcıyı oluştur.
5. APK menüsündeki `Web Sitesi` butonunun doğru adrese gitmesi için:
   - proje kökünde `APK_WEB_URL_AYARLA.bat` çalıştır
   - login URL gir (`https://.../login`)
   - sonra `APK_URET_VE_KLASOR_AC.bat` ile APK üretip kur.

## Supabase Kurulum (ücretsiz senkron için)

1. Supabase projesinde `SQL Editor` aç.
2. Bu dosyanın içeriğini yapıştırıp çalıştır:
   - `web-portal/SUPABASE_SETUP.sql`
3. Sonra `SUPABASE_ENV_AYARLA.bat` çalıştır:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
4. Ardından `START_WEB_PORTAL.bat` ile portalı başlat.

## Ortam Değişkenleri
- `SECRET_KEY` : Zorunlu olarak güçlü bir değer verin (canlıda)
- `DATABASE_URL` : Örn `sqlite:///mesai_web.db` veya PostgreSQL bağlantısı
- `APK_URL` : Panelde görünen APK indirme URL'si
- `RESET_TOKEN_EXPIRE_MIN` : Şifre sıfırlama link süresi (dk)
- `SITE_BASE_URL` : Örn `https://portal.seninalanin.com`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`
- `COOKIE_SECURE` : canlıda `true` olmalı (HTTPS varsa)
- `MAX_UPLOAD_MB` : içe aktarma dosya limiti
- `SUPABASE_URL` : örn `https://xxxx.supabase.co`
- `SUPABASE_ANON_KEY` : `sb_publishable_...`

## Canlı Yayın Önerisi
- Backend: Gunicorn + Nginx (Linux VPS) veya Waitress (Windows)
- Veritabanı: PostgreSQL
- Domain + SSL: Cloudflare + Let's Encrypt

## Android Senkronu Sonraki Adım
Mevcut Android uygulamasına:
- `/api/login`
- `/api/entries` GET/POST/PUT/DELETE
endpointleri bağlanarak çift yönlü senkron tamamlanır.

## APK/WEB Uyumlu İçe Aktar
- APK’den alınan yedek JSON dosyası, web `Raporlar` ekranındaki `İçe Aktar` ile yüklenebilir.
- Profil bilgileri + mesai kayıtları uyumlu şekilde aktarılır.
