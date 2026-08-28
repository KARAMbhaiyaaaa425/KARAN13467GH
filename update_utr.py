import os
filepath = r'C:\Users\Acer\.gemini\antigravity\scratch\FAMPAY-WEB-DASHBOARD\templates\checkout.html'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Add UTR Modal HTML right after Error Modal
error_modal_html = '''        <!-- Error Modal -->
        <div class="fixed inset-0 bg-black/80 flex items-center justify-center z-50 hidden transition-opacity" id="errorModal">
            <div class="bg-gray-900 border border-red-500/30 rounded-2xl p-6 w-full max-w-sm mx-4 transform scale-95 opacity-0 transition-all duration-300" id="errorModalContent">
                <div class="text-red-500 mb-4 text-center">
                    <i class="fas fa-exclamation-triangle text-5xl"></i>
                </div>
                <h3 class="text-xl font-bold text-white text-center mb-2">Payment Not Found</h3>
                <p class="text-gray-400 text-center mb-6 text-sm">We couldn't verify your payment yet. Please ensure you have paid the exact amount.</p>
                <button onclick="closeErrorModal()" class="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 rounded-xl transition-colors">
                    Try Again
                </button>
            </div>
        </div>'''

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
    content = content.replace(error_modal_html, error_modal_html + "\n" + utr_modal_html)

# Update Javascript
verify_logic_old = '''        // Manual Verify Button Logic (15 sec dynamic polling)
        document.getElementById('verifyBtn').addEventListener('click', async () => {
            const btn = document.getElementById('verifyBtn');
            const originalHTML = btn.innerHTML;
            btn.innerHTML = 'Wait <i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;
            
            let attempts = 0;
            const maxAttempts = 10; // 10 attempts * 1.5s = 15 seconds
            
            const verifyLoop = setInterval(async () => {
                attempts++;
                try {
                    const res = await fetch(`/api/verify?api_key=${apiKey}&txn_id=${txnId}`);
                    const data = await res.json();
                    
                    if (data.status === 'completed') {
                        clearInterval(verifyLoop);
                        clearInterval(checkInterval);
                        clearInterval(timerInterval);
                        document.getElementById('success_utr').textContent = data.data.utr || 'Auto-Verified';
                        document.getElementById('successScreen').classList.add('active');
                        
                        const callbackUrl = document.getElementById('callback_url').value;
                        if (callbackUrl && callbackUrl !== 'None' && callbackUrl !== '') {
                            setTimeout(() => {
                                window.location.href = callbackUrl;
                            }, 2500);
                        }
                    } else if (attempts >= maxAttempts) {
                        clearInterval(verifyLoop);
                        btn.innerHTML = originalHTML;
                        btn.disabled = false;
                        showErrorModal();
                    }
                } catch (e) {
                    if (attempts >= maxAttempts) {
                        clearInterval(verifyLoop);
                        btn.innerHTML = originalHTML;
                        btn.disabled = false;
                        showErrorModal();
                    }
                }
            }, 1500);
        });'''

verify_logic_new = '''        function showUtrModal() {
            const modal = document.getElementById('utrModal');
            const content = document.getElementById('utrModalContent');
            modal.classList.remove('hidden');
            setTimeout(() => {
                content.classList.remove('scale-95', 'opacity-0');
                content.classList.add('scale-100', 'opacity-100');
            }, 10);
        }

        function closeUtrModal() {
            const modal = document.getElementById('utrModal');
            const content = document.getElementById('utrModalContent');
            content.classList.remove('scale-100', 'opacity-100');
            content.classList.add('scale-95', 'opacity-0');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 200);
        }

        async function submitUtrManual() {
            const utr = document.getElementById('utrInput').value.trim();
            if(!utr) return alert("Please enter UTR");
            
            const btn = document.getElementById('submitUtrBtn');
            const orig = btn.innerHTML;
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;
            
            try {
                await fetch('/api/submit_utr', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({txn_id: txnId, utr: utr})
                });
                closeUtrModal();
                alert("UTR Submitted! Please wait a few seconds for verification.");
                // Background checkInterval will continue polling and catch it
            } catch(e) {
                alert("Error submitting UTR");
            }
            btn.innerHTML = orig;
            btn.disabled = false;
        }

        // Manual Verify Button Logic (18 sec dynamic polling)
        document.getElementById('verifyBtn').addEventListener('click', async () => {
            const btn = document.getElementById('verifyBtn');
            const originalHTML = btn.innerHTML;
            btn.innerHTML = 'Wait <i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;
            
            let attempts = 0;
            const maxAttempts = 12; // 12 attempts * 1.5s = 18 seconds
            
            const verifyLoop = setInterval(async () => {
                attempts++;
                try {
                    const res = await fetch(`/api/verify?api_key=${apiKey}&txn_id=${txnId}`);
                    const data = await res.json();
                    
                    if (data.status === 'completed') {
                        clearInterval(verifyLoop);
                        clearInterval(checkInterval);
                        clearInterval(timerInterval);
                        document.getElementById('success_utr').textContent = data.data.utr || 'Auto-Verified';
                        document.getElementById('successScreen').classList.add('active');
                        
                        const callbackUrl = document.getElementById('callback_url').value;
                        if (callbackUrl && callbackUrl !== 'None' && callbackUrl !== '') {
                            setTimeout(() => {
                                window.location.href = callbackUrl;
                            }, 2500);
                        }
                    } else if (attempts >= maxAttempts) {
                        clearInterval(verifyLoop);
                        btn.innerHTML = originalHTML;
                        btn.disabled = false;
                        showUtrModal(); // Changed from showErrorModal to showUtrModal
                    }
                } catch (e) {
                    if (attempts >= maxAttempts) {
                        clearInterval(verifyLoop);
                        btn.innerHTML = originalHTML;
                        btn.disabled = false;
                        showUtrModal();
                    }
                }
            }, 1500);
        });'''

if 'showUtrModal' not in content:
    content = content.replace(verify_logic_old, verify_logic_new)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated checkout.html")
