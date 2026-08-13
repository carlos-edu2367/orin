# Chrome Electron e sidebar da Home

No Windows, o menu padrão File/Edit/View do Electron foi removido com
`Menu.setApplicationMenu(null)`. A janela usa `titleBarStyle: hidden` e
`titleBarOverlay` para manter apenas os controles nativos de janela integrados
ao tema do Orin; o cabeçalho recebe área de arraste e os controles da interface
continuam como `no-drag`.

O logo de `frontend/src/assets/orin-logo.png` é duplicado em
`desktop/electron/assets` como PNG para a janela e ICO para electron-builder.
Na Home, a navegação deixou de ser fixa e passou a ser uma coluna da grid entre
o cabeçalho e o rodapé. Isso reserva espaço horizontal para o composer e limita
a rolagem da lista ao painel, sem sobreposição.
