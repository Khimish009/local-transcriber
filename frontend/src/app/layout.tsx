import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Local Transcriber",
  description: "Локальная расшифровка встреч с разделением по спикерам",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
