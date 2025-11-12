// Gmail draft helper for Pedidos
function openGmailDraft(to, subject, body){
  const url = `https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(to||'')}&su=${encodeURIComponent(subject||'')}&body=${encodeURIComponent(body||'')}`;
  window.open(url, '_blank');
}

(function(){
  const btn = document.getElementById('gmail-draft-btn');
  if(btn){
    btn.addEventListener('click', ()=>{
      const cliente = document.getElementById('pedido_cliente')?.value||'';
      const sucursal = document.getElementById('pedido_sucursal')?.value||'';
      const mail = document.getElementById('pedido_mail_pedido')?.value||'';
      const nota = document.getElementById('pedido_nota')?.value||'';
      const desc = document.getElementById('pedido_descripcion')?.value||'';
      const subject = `Pedido a ${cliente}${sucursal? ' | Sucursal: '+sucursal:''}`;
      const body = `Nota:\n${nota}\n\nDescripción del pedido:\n${desc}\n\nAtentamente,\nSirio Arabia`;
      openGmailDraft(mail, subject, body);
    });
  }
})();
