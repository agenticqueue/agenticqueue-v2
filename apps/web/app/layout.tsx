import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgenticQueue 2.0",
  description: "AgenticQueue 2.0 health surface",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
