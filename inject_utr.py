import os

filepath = r'C:\Users\Acer\.gemini\antigravity\scratch\FAMPAY-WEB-DASHBOARD\templates\checkout.html'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

utr_modal_html = '''        <!-- UTR Modal -->
        <div class="fixed inset-0 bg-black/80 flex items-center justify-center z-50 hidden transition-opacity" id="utrModal">
            <div class="bg-gray-900 border border-blue-500/30 rounded-2xl p-6 w-full max-w-sm mx-4 transform scale-95 opacity-0 transition-all duration-300" id="utrModalContent">
                <div class="text-blue-500 mb-4 text-center">
                    <i class="fas fa-file-invoice-dollar text-5xl"></i>
                </div>
                <h3 class="text-xl font-bold text-white text-center mb-2">Submit UTR / Ref No</h3>
                <p class="text-gray-400 text-center mb-4 text-sm">We couldn't verify your payment automatically. Please enter your 12-digit UTR or Reference Number.</p>
                <input type="text" id="utrInput" placeholder="Enter 12-digit UTR..." class="w-full bg-black/50 border border-white/10 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-blue-500 mb-4 font-mono text-center">
                <div class="flex space-x-3">
                    <button onclick="closeUtrModal()" class="w-1/2 bg-gray-700 hover:bg-gray-600 text-white font-bold py-3 rounded-xl transition-colors">
                        Cancel
                    </button>
                    <button onclick="submitUtrManual()" class="w-1/2 bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl transition-colors" id="submitUtrBtn">
                        Submit
                    </button>
                </div>
            </div>
        </div>'''

if 'id="utrModal"' not in content:
    content = content.replace('<!-- Success Screen -->', utr_modal_html + '\n\n        <!-- Success Screen -->')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Added UTR HTML.")
else:
    print("UTR HTML already present.")
