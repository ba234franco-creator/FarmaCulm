let carrito = [];
let total = 0;
function cambiarCantidad(btn, cambio) {
    const span = btn.parentElement.querySelector(".cantidad");
    let valor = parseInt(span.textContent) + cambio;
    if (valor < 1) valor = 1;
    span.textContent = valor;
}
function agregarProducto(btn, nombre, precio) {
    const cantidad = parseInt(btn.previousElementSibling.querySelector(".cantidad").textContent);
    const existe = carrito.find(p => p.nombre === nombre);

    if (existe) {
        existe.cantidad += cantidad;
    } else {
        carrito.push({ nombre, precio, cantidad });
    }

    total += precio * cantidad;
    actualizar();
    const originalText = btn.innerHTML;
    btn.innerHTML = "¡Añadido!";
    setTimeout(() => btn.innerHTML = originalText, 800);
}
function actualizar() {
    const lista = document.getElementById("listaCarrito");
    const totalTxt = document.getElementById("total");
    const btnPedido = document.getElementById("btnPedido");
    const cartCount = document.getElementById("cartCount");

    lista.innerHTML = "";
    let mensaje = "Hola FARMACULM, mi pedido es:%0A%0A";

    carrito.forEach((p, index) => {
        lista.innerHTML += `
            <li>
                <span>${p.nombre} (x${p.cantidad})</span>
                <span>$${(p.precio * p.cantidad).toFixed(2)}</span>
            </li>`;
        mensaje += `• ${p.nombre} x${p.cantidad} ($${(p.precio * p.cantidad).toFixed(2)})%0A`;
    });

    totalTxt.textContent = `Total a pagar: $${total.toFixed(2)}`;
    mensaje += `%0A*TOTAL: $${total.toFixed(2)}*`;

    if (btnPedido) {
        btnPedido.href = `https://wa.me/573194982275?text=${mensaje}`;
    }
    if (cartCount) {
        cartCount.textContent = carrito.reduce((acc, p) => acc + p.cantidad, 0);
    }
}
document.getElementById("searchInput").addEventListener("keyup", function() {
    const q = this.value.toLowerCase();
    document.querySelectorAll(".producto").forEach(p => {
        const nombre = p.querySelector("h3").textContent.toLowerCase();
        p.style.display = nombre.includes(q) ? "block" : "none";
    });
});

const backToTop = document.getElementById("backToTop");
window.addEventListener("scroll", () => {
    // Usamos classList para animaciones CSS
    if (window.scrollY > 300) {
        backToTop.style.display = "block"; // Asegura visibilidad
        backToTop.classList.add("show");
    } else {
        backToTop.classList.remove("show");
    }
});

backToTop.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
});
const backToTopBtn = document.getElementById("backToTop");

window.onscroll = function() {
    if (document.body.scrollTop > 300 || document.documentElement.scrollTop > 300) {
        backToTopBtn.style.display = "block";
    } else {
        backToTopBtn.style.display = "none";
    }
};

backToTopBtn.onclick = function() {
    window.scrollTo({
        top: 0,
        behavior: 'smooth'
    });
};