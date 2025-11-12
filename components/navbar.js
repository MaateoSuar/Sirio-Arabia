class CustomNavbar extends HTMLElement {
    connectedCallback() {
        this.attachShadow({ mode: 'open' });
        this.shadowRoot.innerHTML = `
            <style>
                .navbar {
                    height: 64px;
                    background-color: #1e3a8a;
                    color: white;
                    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                }
                
                .logo {
                    height: 40px;
                }
                
                .user-avatar {
                    width: 36px;
                    height: 36px;
                }
                
                @media (max-width: 768px) {
                    .navbar {
                        padding-left: 1rem;
                        padding-right: 1rem;
                    }
                    
                    .logo-text {
                        display: none;
                    }
                }
            </style>
            <nav class="navbar flex items-center justify-between px-6 w-full fixed top-0 z-50">
                <div class="flex items-center space-x-4">
                    <div class="flex items-center">
                        <img src="http://static.photos/business/200x200/1" alt="Logo" class="logo rounded-full mr-2">
                        <span class="logo-text text-xl font-bold">ArabiaTrack</span>
                    </div>
                </div>
                
                <div class="flex items-center space-x-4">
                    <div class="relative">
                        <i data-feather="bell" class="text-white"></i>
                        <span class="absolute top-0 right-0 block h-2 w-2 rounded-full bg-red-500"></span>
                    </div>
                    <div class="flex items-center space-x-2">
                        <img src="http://static.photos/people/200x200/1" alt="User" class="user-avatar rounded-full">
                        <span class="hidden md:inline text-sm font-medium">Sirio Arabia</span>
                    </div>
                </div>
            </nav>
        `;
    }
}

customElements.define('custom-navbar', CustomNavbar);