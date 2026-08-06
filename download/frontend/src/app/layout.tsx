import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "مراقب الروابط | WhatsApp & Telegram Monitor",
  description: "نظام سحب روابط واتساب وتيليجرام من المجموعات الجامعية",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ar" dir="rtl" className="dark">
      <body className="antialiased">{children}</body>
    </html>
  );
}
