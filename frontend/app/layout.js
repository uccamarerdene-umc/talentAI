import './globals.css'

export const metadata = {
  title: 'Talent AI Зөвлөх',
  description: 'Бодит өгөгдөлд тулгуурласан хиймэл оюун ухаант туслах',
}

export default function RootLayout({ children }) {
  return (
    <html lang="mn">
      <head>
        <link
          rel="stylesheet"
          href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css"
        />
      </head>
      <body>{children}</body>
    </html>
  )
}
