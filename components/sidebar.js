class CustomSidebar extends HTMLElement {
    connectedCallback() {
        this.attachShadow({ mode: 'open' });
        this.shadowRoot.innerHTML = `
            <style>
                .sidebar {
                    width: 240px;
                    background-color: white;
                    box-shadow: 2px 0 4px rgba(0, 0, 0, 0.05);
                }
                
                .nav-item {
                    padding: 0.75rem 1.5rem;
                    color: #4b5563;
                    border-left: 3px solid transparent;
                }
                
                .nav-item:hover {
                    background-color: #f3f4f6;
                    color: #1e40af;
                }
                
                .nav-item.active {
                    background-color: #eff6ff;
                    color: #1e40af;
                    border-left-color: #1e40af;
                    font-weight: 500;
                }
                
                .nav-item i {
                    margin-right: 0.75rem;
                }
                
                @media (max-width: 768px) {
                    .sidebar {
                        width: 72px;
                    }
                    
                    .nav-text {
                        display: none;
                    }
                    
                    .nav-item {
                        padding: 1rem;
                        justify-content: center;
                    }
                    
                    .nav-item i {
                        margin-right: 0;
                    }
                }
            </style>
            <aside class="sidebar h-screen fixed top-16 pt-4 hidden md:block">
                <nav>
                    <a href="index.html" class="nav-item flex items-center active">
                        <i data-feather="home"></i>
                        <span class="nav-text">Dashboard</span>
                    </a>
                    <a href="clientes.html" class="nav-item flex items-center">
                        <i data-feather="users"></i>
                        <span class="nav-text">Clientes</span>
                    </a>
                    <a href="empresas.html" class="nav-item flex items-center">
                        <i data-feather="briefcase"></i>
                        <span class="nav-text">Empresas</span>
                    </a>
                    <a href="pedidos.html" class="nav-item flex items-center">
                        <i data-feather="shopping-cart"></i>
                        <span class="nav-text">Pedidos</span>
                    </a>
                    <a href="status.html" class="nav-item flex items-center">
                        <i data-feather="truck"></i>
                        <span class="nav-text">Status Mercadería</span>
                    </a>
                    <a href="cobranzas.html" class="nav-item flex items-center">
                        <i data-feather="dollar-sign"></i>
                        <span class="nav-text">Cobranzas</span>
                    </a>
                    <a href="historial.html" class="nav-item flex items-center">
                        <i data-feather="archive"></i>
                        <span class="nav-text">Historial</span>
                    </a>
                    <a href="crm.html" class="nav-item flex items-center">
                        <i data-feather="activity"></i>
                        <span class="nav-text">CRM</span>
                    </a>
                </nav>
                
                <div class="absolute bottom-0 w-full p-4 border-t">
                    <a href="#" class="flex items-center text-gray-500">
                        <span class="nav-text">Ajustes</span>
                    </a>
                </div>
            </aside>
        `;
        if (window.feather) {
            window.feather.replace();
        }
    }
}

customElements.define('custom-sidebar', CustomSidebar);