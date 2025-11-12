// Global variables
let clients = [];
let companies = [];
let orders = [];
let shipments = [];
let collections = [];

// DOM Ready
document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Load sample data (in a real app, this would be from an API)
    loadSampleData();
    
    // Initialize datepickers
    initDatePickers();
    
    // Set up event listeners
    setupEventListeners();
});

function loadSampleData() {
    // Sample clients
    clients = [
        {
            id: 1,
            lastName: "Pérez",
            firstName: "Juan",
            company: "Empresa XYZ",
            branch: "Sucursal Centro",
            joinDate: "2022-01-15",
            phone: "+54 11 1234-5678",
            email: "juan.perez@example.com",
            currentCompanies: ["Empresa XYZ", "Empresa ABC"],
            pastCompanies: ["Empresa DEF"],
            potentialCompanies: ["Empresa GHI"]
        },
        {
            id: 2,
            lastName: "Gómez",
            firstName: "María",
            company: "Empresa ABC",
            branch: "Sucursal Norte",
            joinDate: "2021-11-20",
            phone: "+54 11 8765-4321",
            email: "maria.gomez@example.com",
            currentCompanies: ["Empresa ABC"],
            pastCompanies: ["Empresa XYZ"],
            potentialCompanies: ["Empresa JKL"]
        }
    ];

    // Sample companies
    companies = [
        {
            id: 1,
            name: "Empresa XYZ",
            avgDispatchDelay: 7,
            avgPaymentTerm: 30,
            orderEmail: "pedidos@xyz.com",
            paymentEmail: "cobranzas@xyz.com",
            currentClients: ["Juan Pérez"],
            pastClients: ["María Gómez"],
            potentialClients: ["Carlos Rodríguez"]
        },
        {
            id: 2,
            name: "Empresa ABC",
            avgDispatchDelay: 5,
            avgPaymentTerm: 15,
            orderEmail: "compras@abc.com",
            paymentEmail: "facturacion@abc.com",
            currentClients: ["María Gómez"],
            pastClients: ["Juan Pérez"],
            potentialClients: ["Ana Martínez"]
        }
    ];

    // Sample orders
    orders = [
        {
            id: 1,
            date: "2023-05-10",
            clientId: 1,
            branch: "Sucursal Centro",
            companyId: 1,
            note: "Urgente para fin de mes",
            description: "10 unidades de producto A, 5 de producto B",
            price: null,
            paymentMethod: "Transferencia"
        }
    ];

    console.log("Sample data loaded");
}

function initDatePickers() {
    // Initialize flatpickr for date inputs
    const dateInputs = document.querySelectorAll('input[type="date"]');
    dateInputs.forEach(input => {
        input.flatpickr({
            dateFormat: "Y-m-d",
            allowInput: true
        });
    });
}

function setupEventListeners() {
    // Setup event delegation for dynamic elements
    document.addEventListener('click', function(e) {
        // Handle edit buttons
        if (e.target.closest('.edit-btn')) {
            const itemId = e.target.closest('.edit-btn').dataset.id;
            // Handle edit logic
            console.log(`Edit item ${itemId}`);
        }
        
        // Handle delete buttons
        if (e.target.closest('.delete-btn')) {
            const itemId = e.target.closest('.delete-btn').dataset.id;
            // Handle delete logic
            console.log(`Delete item ${itemId}`);
        }
    });
}

// Utility function to format dates
function formatDate(dateString) {
    const options = { year: 'numeric', month: 'short', day: 'numeric' };
    return new Date(dateString).toLocaleDateString('es-AR', options);
}

// Export functions for use in other modules
window.ArabiaTrack = {
    formatDate,
    clients,
    companies,
    orders,
    shipments,
    collections
};