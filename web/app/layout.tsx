import "./globals.css";

export const metadata = { title: "Prior auth reviewer" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header>
          <div className="bar">
            <a href="/"><strong>Prior auth reviewer</strong></a>
            <span>adalimumab for rheumatoid arthritis · synthetic Synthea patients</span>
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
