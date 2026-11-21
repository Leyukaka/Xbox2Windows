# Experiment notes and reproduction commands

Run commands from the repository root. Start with the [project overview](../README.md) and [contribution guide](../CONTRIBUTING.md). Package-specific observations below used local inputs. Community regression tests use fixtures built from this repository.

## Local tools

For optional static dependency inspection, build Microsoft's pinned version of xbdepends from PowerShell with x64 Visual Studio Build Tools, CMake and the Windows SDK.

```powershell
.\tools\setup-xbdepends.ps1
```

Load the toolchain in each new development shell, then configure, build and test.

```powershell
.\tools\dev.ps1
cmake --preset dev
cmake --build --preset dev
ctest --preset dev
```

The tests exercise desktop D3D12 root signature serialization and deserialization. Use the resulting test binary to try the static inspection tools.

```powershell
xbdepends -v .\out\build\dev\d3d12_sdk_check.exe
dumpbin /imports .\out\build\dev\d3d12_sdk_check.exe
```

Build outputs and downloaded tool sources live under `out/`. [Microsoft xbdepends documentation](https://learn.microsoft.com/en-us/samples/microsoft/xbox-gdk-samples/xbdepends/)

## Offline CPU leaf recompilation

With the Python-enabled Windows dev preset configured, run:

```powershell
cmake --build --preset dev --target cpu_leaf_original_lifted cpu_leaf_inverted_lifted
ctest --preset dev --no-tests=error -R '^cpu_leaf_aot_branches$' -V
```

MSVC's x64 assembler and linker build [the original function](../tests/cpu/branch.asm) into a DLL. The [translator](../tools/cpu_leaf_aot.py) reads its exported entry RVA and the matching runtime-function interval from the PE. It translates those bytes into C++, which MSVC compiles to a separate x64 object and links into a second DLL. The intermediate source is in `out/build/dev/cpu_leaf_original_lifted.cpp`.

The supported signature takes an unsigned 32-bit integer in ECX and a writable word pointer in RDX, and returns a 32-bit integer in EAX. The decoder covers the fixture's register move, immediate compare paired with unsigned short JB/JAE, short forward jump, immediate add/subtract, output store and return. It rejects unknown encodings and branches outside the decoded instruction boundaries. Leaf functions can omit unwind metadata. This experiment uses an explicit interval provided by the assembler.

The Windows test calls both DLLs with the same 263 inputs. Five take the below-five branch and 258 take the other branch, including equality, the unsigned sign boundary and wraparound. It compares the returned word, the stored word and guards on either side, and checks the original against independent unsigned arithmetic. A control assembles the same source with the conditional jump inverted and runs that PE through the same translator. Its results must differ from the original on every input. [Direct-call follow-up](https://github.com/R0Y00/Xbox2Windows/issues/22)

## Chained function ranges and early returns

The range reader groups runtime-function entries by their complete unwind-chain parent records. For chained trailer lookup it accepts both version 1 and version 2 `UNWIND_INFO` headers when `UNW_FLAG_CHAININFO` is the only flag set, without decoding general version-2 unwind codes. The early-return translator uses iced-x86 to decode each owned interval separately. It follows instruction-aligned branches and fallthrough, models stack slots and preserved registers, and checks their restoration at each return. A neighboring function can occupy the gap. The single-interval translators report split functions explicitly. [Microsoft chained unwind information](https://learn.microsoft.com/en-us/cpp/build/exception-handling-x64#chained-unwind-info-structures)

```powershell
cmake --build --preset dev --target cpu_chained_lifted cpu_register_probe
ctest --preset dev --no-tests=error -R '^cpu_aot_chained_ranges$' -V
```

The source-owned fixture has a primary interval, two levels of chaining and a neighboring export. Both null inputs return 7, and two non-null inputs return 21. A Windows probe compares native returns with the generated result, physical RBX/RSI, and guards. A patched copy verifies version-2 chained header resolution. Negative controls cover broken and cyclic parent links, unsupported chained header versions and combined flags, jumps into a neighbor or instruction interior, and an indirect call.

For the local FidelityFX loader, run `cpu_chained_aot.py <loader.dll> ffxDestroyContext out/local_context.cpp --partial` using the Python dev environment. Compile the generated C++ as a Windows x64 DLL. Its `try_lifted(first, second, output)` returns 1 and writes the guest result for a completed path. Status 0 leaves the output untouched at an unsupported indirect call. Pointer arguments must reference readable fixture storage on paths that dereference them. The local inspection recovered four owned intervals. Running the generated code produced 6 for a null first argument and for a pointer to a null context. A valid non-null control stopped at the first indirect call with its output sentinel intact. The original package DLL was inspected as a file. Native differential results above are from the repository fixture. [Callback follow-up](https://github.com/R0Y00/Xbox2Windows/issues/29)

### Object-table callbacks on Windows

The source-built caller follows a handle to its context and loads a table pointer from that context. Slot 1 takes the context address and a 64-bit input and returns a 64-bit value. The test selects two tables with the providers in opposite orders. It compares the observed provider, both arguments, return and output guards with native Windows execution, including inputs with bit 63 set. Both null paths return 6 without calling either provider.

```powershell
cmake --build --preset dev --target cpu_callback_lifted
ctest --preset dev --no-tests=error -R '^cpu_aot_object_callback$' -V
```

For this fixture, `cpu_chained_aot.py` takes `--callback-offset 8` and emits `try_lifted_callback(first, second, output, binding)`. The host supplies readable fixture storage and a binding containing the expected table address and typed function pointer. Dispatch requires both the table address and the selected slot's target to match. A missing or mismatched binding returns status 0 and leaves the output untouched. The existing three-argument partial profile still stops at an unbound indirect call.

### FidelityFX destroy provider and cleanup

In the inspected Xbox loader, `ffxCreateContext` masks the effect ID and calls the internal resolver at RVA `0x1000`. For effect `0x10000` the resolver gets or loads `amd_fidelityfx_upscaler_xbox.dll`. It resolves the named export `ffxDestroyContext` and stores the address at loader RVA `0x4098`, slot 1 of the table at `0x4090`. After a successful provider create, the loader puts that table pointer and the provider context in the two words of a wrapper. The packaged upscaler export directory independently identifies the destroy entry at provider RVA `0x13a0`. The frame-generation route uses another table and its module is absent from this package.

The loader passes the **address of the wrapper's provider-context field** in RCX and the optional allocation-callbacks pointer in RDX. The upscaler dereferences the first argument and saves the second in a local slot. Its internal destroy method receives the effect object, the context-field address and the address of that saved allocator pointer. On the provider-object release path the upscaler also uses the allocator for deallocation or calls CRT free. These register uses agree with AMD's two-argument destroy declaration and unsigned 32-bit status. [AMD FidelityFX API header](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/main/Kits/FidelityFX/api/include/ffx_api.h)

After provider destroy, the **loader** frees its own wrapper, including when the provider returned an error. Custom cleanup receives the allocator's user data and the wrapper address. The default branch calls the imported CRT free. Both branches return the saved provider status. The explicit `--ffx-destroy` profile binds a stable provider table, allocator and cleanup routines for the duration of the call. The host must supply live storage compatible with the chosen deallocator. An unresolved binding stops before invoking the provider.

```powershell
cmake --build --preset dev --target cpu_destroy_lifted cpu_destroy_provider
ctest --preset dev --no-tests=error -R '^cpu_aot_destroy_contract$' -V
```

CI compares a source-built native caller with its static translation. On Windows the same harness also compared that native reference with the translation of all four owned ranges of the local loader export. Both null exits returned 6. With a source-built provider resolved by export name, both cleanup routes preserved 0, 6, 0x80000001 and 0xffffffff. The test checks the addresses passed to destroy and custom cleanup, and that the provider clears the inner field before the wrapper is released. The packaged upscaler's own effect teardown was inspected statically.

To repeat the local comparison in a VS x64 developer shell, translate the loader with `tools/cpu_chained_aot.py <loader.dll> ffxDestroyContext out/local_destroy.cpp --ffx-destroy`, compile it with `cl /nologo /LD /O2 /Fo:out/local_destroy.obj out/local_destroy.cpp /link /OUT:out/local_destroy.dll /IMPLIB:out/local_destroy.lib`, and run `tests/cpu/destroy_check.py` with absolute paths to `cpu_destroy.dll`, `local_destroy.dll` and `cpu_destroy_provider.dll`, in that order.

### First device calls in the packaged upscaler

For the upscaling effect (`0x10000`), the local upscaler's `ffxCreateContext` export at RVA `0x11d0` takes an output handle pointer in RCX, a descriptor chain in RDX, and an optional allocator in R8. On the inspected path, the backend extension has type `0x01000002` at offset 0, a next pointer at +8 and the device pointer at +16. The provider selection call at `0x129c` passes that pointer to a predicate that returns true without reading it. At `0x1349` the export calls the selected provider's create method at `0xc9b0`.

The provider reads the device from the extension at `0xcdca`, sets up a fresh 0x18f160-byte scratch buffer for one context and invokes its backend callback at `0x2720`. At the call site RCX points to the backend interface, EDX is `0x14`, R8 points to four 32-bit words `{0, 0, 0, 1}`, and R9 points to storage for the output context index. The callback reads the device at interface +0xd0 and scratch at +0xc0. On its first use, the device receives `IUnknown::AddRef(this)` at `0x2803`. It takes only the device in RCX. The callback stores the device at scratch +8 and continues without inspecting the returned reference count.

The first resource creation at `0x2893` uses device vtable slot 14 (+0x70). RCX holds the device, RDX points to a 16-byte `D3D12_DESCRIPTOR_HEAP_DESC`, R8 points to the IID of `ID3D12DescriptorHeap` (`8efb471d-616c-4f49-90f7-127bb763fa51`), and R9 points to the output slot. All five calls request node mask 0. [D3D12 CreateDescriptorHeap](https://learn.microsoft.com/en-us/windows/win32/api/d3d12/nf-d3d12-id3d12device-createdescriptorheap) · [IUnknown AddRef](https://learn.microsoft.com/en-us/windows/win32/api/unknwn/nf-unknwn-iunknown-addref)

| Call RVA | Heap type | Descriptors | Shader visible | Output in scratch |
|---|---|---:|---|---|
| `0x2893` | CBV/SRV/UAV | 512 | No | +0x28 |
| `0x28ac` | CBV/SRV/UAV | 512 | No | +0x30 |
| `0x28cd` | CBV/SRV/UAV | 512 | Yes | +0x38 |
| `0x28fd` | CBV/SRV/UAV | 167,936 | Yes | +0x48 |
| `0x2935` | RTV | 8 | No | +0x20 |

On Windows the probe starts at RVA `0x27f8` with a live device, zero-initialized scratch, a context capacity of one and R15 zero. It executes the offline translation of the package's argument construction and binds the two COM methods to desktop D3D12. The RTX 4060 Laptop (driver 32.0.16.1656) and Microsoft WARP (10.0.26100.9818) each returned `S_OK` five times. The probe verified five distinct live heap handles, their output slots and their descriptions. Both debug-layer runs reported zero errors and zero warnings. The host releases the five heaps and its device reference on exit.

After the fifth heap the test continues into the backend slot scan at RVA `0x2938`. The host stops on a failed HRESULT so that a desktop failure identifies the exact heap. The package slice itself has no intervening HRESULT branches.

To run the local translation in a VS x64 developer shell, set `$upscaler` to the full path of the package's upscaler DLL and use a separate build directory. The translator checks the image hash before generating C++.

```powershell
cmake --preset dev -B out/build/ffx-device "-DFFX_UPSCALER_IMAGE:FILEPATH=$upscaler"
cmake --build out/build/ffx-device --target offscreen_check
ctest --test-dir out/build/ffx-device -R '^ffx_device_init_' --no-tests=error -V
```

The log identifies this run with `source=translated-package`. The default build uses a source-owned desktop control for the five D3D12 calls (`source=desktop-control`). Public CI runs that control on WARP and compares native and translated x64 heap-size arithmetic for six inputs, including 32-bit wraparound.

### Backend context slot selection

The provider takes the address of its 32-bit index field at +0xec (`0xcf94`) and passes it in R9. The backend callback saves that pointer in R14. On first use it finishes setting up the shared descriptor range at `0x2938`. Subsequent calls skip heap creation and rejoin at `0x2942`. The callback increments the 32-bit scratch counter before checking capacity, then scans from index zero. The table pointer is at scratch +0x1078, records are 96 bytes each, and bit 0 of the byte at record +0x3c means occupied.

At the first free slot, the store at `0x2980` writes the index directly to the provider. The callback writes effect `0x14`, sets flag bit 0, clears bit 1 and preserves the other six bits. It initializes two pairs of descriptor cursors to `512*i+1` and `512*i+511`. On the package's one-slot input, the four sizes `{0,0,0,1}` reserve the half-open range `[0x19000,0x19001)`. When all slots are occupied, the scan branches to the same zero-return exit at `0x2a9f` without writing a record or the output index.

| Windows input | Backend return | Provider index after call | Scratch counter | Slot change |
|---|---:|---|---|---|
| Fresh package table after five heaps | 0 | 0 | 0 → 1 | Slot 0 becomes occupied |
| Same table, second request | 0 | `0xfeedface` preserved | 1 → 2 | None |
| Controlled two-slot table, slot 0 occupied | 0 | 1 | 1 → 2 | Slot 1 becomes occupied. Its range starts after slot 0 at `0x32008` |
| Both controlled slots occupied | 0 | `0xfeedface` preserved | 2 → 3 | None |

The package translation and an independently compiled C++ control ran on the RTX 4060 Laptop and WARP using the commands above. The test compares the entire scratch buffer and provider output region, including bytes around the index. The exhaustion cases supply a new output field initialized to the sentinel. The dual-slot setup exercises a nonzero index with a controlled capacity and four nonzero sizes. Public CI runs the desktop control plus a source-built x64 scan and four-lane sum in `cpu_aot_heap_layout`. The same fixture compares 32-bit registers with sign-extended 8-bit immediates such as `cmp ecx,-1`, which the lowerer evaluates at the operand width.

The provider tests EAX at `0xcfc2`. A zero backend return continues initialization. It later loads the same index field into R8D at `0xd1f4` for the resource callback at `0x2e30`. On the default path the first descriptor identifies `FSR4UPSCALER_Recurrent` (ID 13), with its allocation mode and placed-resource flag both zero and the optional allocation callback unset. The intervening descriptor-builder call at `0xc4ff` resolves to a CPU-only helper at `0x7900`, and the import at `0x2ea1` is an XMem working-set query. The next call **on the device** is at `0x33cc`, Xbox vtable slot 76 (+0x260). Its arguments include heap properties, zero heap flags, a resource description, initial state, a null clear value, the `ID3D12Resource` IID and an output pointer. Those arguments match the shape of desktop committed-resource creation. The Windows replay below binds that call to the desktop device. [Desktop CreateCommittedResource signature](https://learn.microsoft.com/en-us/windows/win32/api/d3d12/nf-d3d12-id3d12device-createcommittedresource)

### Creating the first recurrent texture

For the default provider, the builder at `0xbf60` receives the output-size pair in R8. The create descriptor supplies render width/height at +0x14/+0x18 and output width/height at +0x1c/+0x20. The first resource uses the latter pair and has ID 13 and name `FSR4UPSCALER_Recurrent`. The local replay translates the first descriptor stores, the provider's index-output address calculation and the default committed-resource path. It supplies zero for the optional Xbox working-set statistics and uses the default allocator route with the optional resource allocation callback unset.

| Field | Package path | Desktop D3D12 argument |
|---|---|---|
| Dimensions | Kind 2, output width and height, one layer, one mip | Texture2D, one sample, quality 0 |
| Format | Format code 10 selects value 28 in the format switch at `0x2160` | `DXGI_FORMAT_R8G8B8A8_UNORM` |
| Flags | Usage bits 0 and 1 produce flags 0x5 | `ALLOW_RENDER_TARGET` and `ALLOW_UNORDERED_ACCESS` |
| Initial state | Provider state 2 selects value 8 at `0x325a` | `D3D12_RESOURCE_STATE_UNORDERED_ACCESS` |
| Allocation | Default heap, zero heap flags, null optimized clear value | Committed resource, default alignment, single-node masks 1/1 |
| Layout | Xbox texture layout 0x101, extension word 3 and trailing zero | Driver-chosen `D3D12_TEXTURE_LAYOUT_UNKNOWN`. The adapter checks the Xbox extension fields before omitting them from the desktop descriptor |

With a render size of 1280×720, the RTX 4060 Laptop (driver 32.0.16.1656) and WARP (10.0.26100.9818) each ran two fresh contexts. For outputs of 1920×1080 and 1600×900, all four calls returned `S_OK`. Each resource's `GetDesc` reported the requested output dimensions, format 28, flags 0x5, one layer/mip/sample, quality zero, desktop layout 0 and 65,536-byte alignment. Initial state is a creation argument. The probe transitions the texture from UAV to shader read, then copy source, then back to UAV and waits for completion. Both debug layers reported zero errors and zero warnings.

On context slot 0 the provider passes +0x17c as the output for ID 13. The resource callback takes cursor 1, advances it to 2 and writes 1 to the output **before** the device call. On success it records provider state 2 in both state fields and stores the returned COM pointer in backend record 1 at `0x3408`. The test checks that pointer against the live texture and verifies all adjacent resource indices retain their sentinels.

Use the package build directory configured above in a VS x64 developer shell. Public CI runs the source-owned WARP fixture with the same desktop mapping and readback checks. It also compares native and translated descriptor-copy instructions using the MASM fixture.

```powershell
cmake --build out/build/ffx-device --target offscreen_check
ctest --test-dir out/build/ffx-device -R '^ffx_recurrent_' --no-tests=error -V
```

### Recurrent texture views and pixel readback

After the resource pointer is stored, the callback reads its description and builds views for the single-layer, single-mip Texture2D path. The SRV and UAV format switches both preserve `DXGI_FORMAT_R8G8B8A8_UNORM` (28). The SRV exposes mip 0 only, plane 0, minimum LOD 0 and the default RGBA component mapping (`0x1688`). Both UAV descriptors select mip 0 and plane 0 and use a null counter.

| Device call RVA | Descriptor | Heap pointer in scratch | Slot in fresh context 0 |
|---|---|---|---:|
| `0x37aa` | SRV | +0x28, CPU-only | 1 (resource index) |
| `0x3844` | UAV | +0x38, shader-visible | 1 (UAV base + mip) |
| `0x388a` | UAV mirror | +0x30, CPU-only | 1 (UAV base + mip) |

The resource record stores the UAV base at +0x34 and the count at +0x38, both 1. After creating both UAV descriptors, the callback advances the context's UAV cursor at +0x0c from 1 to 2. Each destination is the heap's CPU start plus the slot times the device-reported descriptor increment. That increment was 32 bytes on the RTX 4060 Laptop and 64 bytes on WARP.

The same `ffx_recurrent_hardware` and `ffx_recurrent_warp` commands above run the pixel check. A source-built compute shader writes a coordinate-dependent RGBA pattern through the original shader-visible UAV. A second pass writes a different pattern through a copy of the CPU UAV mirror. For shader access the probe copies the CPU-only SRV and UAV mirror into a separate, shader-visible test heap. A read shader samples the SRV at texel centers and writes packed RGBA bytes to a buffer. A separate `CopyTextureRegion` reads the original resource pointer directly. After a fence, both paths must match the CPU's expected RGBA value for every pixel. [D3D12 descriptor copying](https://learn.microsoft.com/en-us/windows/win32/direct3d12/copying-descriptors)

Both adapters passed both patterns at 1920×1080 and 1600×900. Each adapter checked 7,027,200 pixels through the SRV and the same number through direct copies, with zero mismatches, debug errors or warnings. The local build executes the package-derived view creation through the cursor update at RVA `0x389b`. Public Windows CI runs the source-owned WARP control. The test shaders exercise descriptor access. The provider loop is covered below.

### Provider resource loop and returned handles

At 1280×720 render and 1920×1080 output, the default provider visits twelve candidate descriptors in order. The builder at RVA `0xbf60` emits 104 bytes per candidate. The loop at `0xd1d0` skips ID 23 when bit 9 of the create flags is clear. The index destination is `provider + 0x148 + 4 * ID`. Three callbacks also allocate an upload resource from the same cursor.

| ID | Resource (FSR4UPSCALER_ prefix) | Shape | Default index | Index with flag 0x200 |
| ---: | --- | --- | ---: | ---: |
| 13 | Recurrent | 1920×1080 RGBA8 | 1 | 1 |
| 14 | History | 1920×1080 RGBA16F | 2 | 2 |
| 15 | HistoryReprojected | 1920×1080 RGBA16F | 3 | 3 |
| 16 | RcasIntermediary | 1920×1080 RGB9E5 | 4 | 4 |
| 5 | AutoExposure | 8-byte raw buffer | 5 | 5 |
| 17 | DefaultExposure | 1×1 RG32F | 7 | 7 |
| 6 | SpdAtomicCounter | 1×1 R32_UINT | 9 | 9 |
| 7 | Luma_Mip_5 | 80×45 R32F | 11 | 11 |
| 22 | ScratchBuffer | 21,045,760-byte raw buffer | 12 | 12 |
| 20 | InitializerBuffer | 131,072-byte raw buffer | 13 | 13 |
| 21 | InitializerBuffer_Upload | 131,072-byte upload buffer | 14 | 14 |
| 23 | DebugInformation | 1920×1080 R16_UNORM | 0 (skipped) | 15 |

The uploads for IDs 5, 17 and 6 occupy slots 6, 8 and 10 respectively. ID 21 is a named provider resource with its own index. The package row loop treats the zero height of ID 5 as zero rows to initialize, despite its eight-byte input payload. ID 17 supplies two float ones and ID 6 supplies a zero fill. The desktop binding uses committed resources, creates SRVs and paired UAVs for eligible resources, and submits the initialization copies with a fence. Default-heap buffers use desktop COMMON state and implicit promotion.

On success, the translated loop copies all 25 indices to the mirror at +0x1ac and writes the provider pointer through the output argument at RVA `0xd263`. The loader returns a separate 16-byte wrapper containing the callback table and that same provider pointer. With flags 0 and 0x200 both adapters returned 0, with next resource indices 15 and 16 respectively. Injecting a callback return of 7 at ID 20 returned 3 through the provider and loader, left both output handles null, preserved the previous resource indices, and left the mirror untouched. The test checks every live backend record against its D3D12 object, both index tables, and the loader wrapper fields. All three cases completed with zero debug errors or warnings on the RTX 4060 Laptop and WARP.

The first desktop format difference occurs at ID 16. WARP exposed typed RGB9E5 UAV stores. The RTX driver (32.0.16.1656) lacked that capability, so the binding created an R32_UINT texture and views for the packed bits. The texel test below checks the two storage formats.

The local build translates the descriptor builder, debug flag, loop and writeback, resource-slot reservation, upload-descriptor construction, and selected create-export branches. Registry selection and the backend device callbacks are bound on the host. When the pinned loader DLL is beside the upscaler DLL, the probe also executes the recompiled loader create path against that provider. Public CI runs the source-owned provider control on WARP. Configure the local package build as in the recurrent texture test above and run:

```powershell
cmake --build out/build/ffx-device --target offscreen_check
ctest --test-dir out/build/ffx-device -R '^ffx_provider_loop_' --no-tests=error -V
```

### RCAS intermediate texel access

The provider-loop CTests above also exercise resource ID 16 after successful context creation. The test uses the provider's UAV and SRV for the intermediate at resource index 4. A source-built compute shader writes a strip of 1,024 HDR colors into the 1920×1080 texture. Another shader loads the texels through the SRV. A separate texture copy returns the underlying 32-bit words.

On the RTX 4060 Laptop the writer packs RGB into an R32_UINT UAV and the reader unpacks it to floats. The shared HLSL helpers clamp finite inputs to [0, 65408], choose a shared exponent and round the three 9-bit mantissas to nearest even. On WARP the typed RGB9E5 views perform the conversion. A second WARP context forces the packed path to exercise the fallback in public CI. A double-precision CPU reference checks the stored bits and all four decoded components, including the implied alpha of 1. The inputs cover all 32 exponents, channel permutations, half steps, exponent carry, underflow, negative values and saturation. The native reference also accepts the documented nearest rounding alternative at ties. [Direct3D RGB9E5 conversion](https://microsoft.github.io/DirectX-Specs/d3d/archive/D3D11_3_FunctionalSpec.htm#19.3.2)

On both local adapters the storage words and decoded channels matched nearest-even on every input. Both debug layers reported zero warnings and errors. The package supplies the local resource-creation path. These shaders test integer-coordinate texel loads. The next experiment traces the first package writer and reader of this intermediate.

The integrated AMD Radeon(TM) Graphics of a Ryzen 5 7600 (RDNA 2, device `164e`, driver 32.0.21045.29003) reports typed UAV stores for RGB9E5, so the native path runs on a physical GPU. Its stores round the mantissas toward zero. In 317 of the 1,024 inputs this gives a different word from nearest-even, and every stored word matches a round-toward-zero encoding. The SRV loads decode those words exactly. The forced packed context on the same adapter still matches nearest-even. For FLOAT -> RGBE, section 19.3.2.2 of the D3D11.3 functional spec says conversion "should be done with nearest even. Round to nearest is allowed", and notes that hardware does not perform it. Its general rule for conversion to another float format, in section 3.2.2, is round-to-zero. The native check therefore accepts nearest-even, round-to-nearest or round-toward-zero and reports how many texels needed each alternative. A store that mixes the two alternatives fails. The Xbox Series X GPU is also RDNA 2; whether its native stores round the same way is untested.

```powershell
ctest --preset dev --no-tests=error -R '^ffx_provider_loop_hardware$' -V
```

### FSR4 dispatch through the RCAS input

With sharpening enabled, the default i8 graph first touches the physical ID 16 texture in `FFX_FSR4_PASS_26`. It writes `rw_mlsr_output_color` (logical ID 10, UAV `u2`). The following `FFX_FSR4_RCAS_PASS` reads `r_rcas_input` (ID 16, SRV `t18`) and writes the external `rw_rcas_output` (ID 11, UAV `u11`). Each RCAS lane computes four output pixels. For each pixel it loads the center and four axial neighbors at integer texel coordinates. The native program contains 20 RGB `image_load` instructions with half-precision returns, four stores and zero sampler bindings.

| Provider RVA | Evidence from the pinned upscaler |
|---|---|
| `0xed10`, `0x1220f`, `0x13278` | Dispatch copies the sharpening byte into the staged constants at offset `0x5c`. When enabled, mirror entry 10 receives primary entry 16. When disabled, mirror entries 10 and 11 both receive the external output. |
| `0x1357d`, `0x13752`, `0x1375f` | The graph builder supplies workgroup counts. The scheduling loop indexes the pipeline array with stride `0x9690` and calls `0xe1d0`. The reflection check walks all 27 graph shader records in order. Case 26 selects the container at `0xa0e90`. |
| `0xe244`, `0xe2b9`, `0xe363`, `0xe3cd` | The scheduler resolves SRVs through the primary resource array and UAVs through the mirror, sets the job type to compute (2), then calls the enqueue callback. |
| `0x137b2`, `0x13898`, `0x138ac` | The sharpening flag gates the extra RCAS job. The separate pipeline at provider offset `0x248` selects the RCAS container at `0x701b0`. The provider then calls the execute callback. |

The package build translates the x64 alias updates and the two grid calculations. At 1920×1080 they produce 60×34×1 and 120×68×1 groups. The GPU replay uses an 8×4 logical image, one group per job, on the actual provider intermediate at index 4. Pass 26 has 256 threads per group and RCAS has 64. The host binds source-owned scene and history inputs as RGB buffers, uses unit exposure, and uses the provider's history and recurrent UAVs. Eight active lanes take the producer's direct texture path. The next experiment connects the two immediately preceding graph jobs through scratch.

Both desktop shaders come from offline decoding of the pinned native streams (3,482 producer instructions before specialization and 437 RCAS instructions). LLVM 15's gfx1030 decoder covers the producer's i8 dot instructions and its gfx1013 decoder covers RCAS. The translator keeps the original packed literal DWORDs, including the zero high half of the packed offset that decrements x. [AMD RDNA 2 instruction set](https://www.amd.com/content/dam/amd/en/documents/radeon-tech-docs/instruction-set-architectures/rdna2-shader-instruction-set-architecture.pdf)

The GPU test checks all 32 intermediate texels and 257 surrounding guard texels. It then compares all 256 final pixels, including edges between different colors, with an independent FP32 five-tap reference (per-channel tolerance 2.5% plus 0.00005 for packed-half arithmetic). Alpha must equal red. RTX 4060 Laptop passed using R32_UINT. WARP passed with native RGB9E5 and with forced R32_UINT. Both debug layers reported zero errors and warnings. These are controlled single-group results.

From a VS x64 developer shell at the repository root, set `$upscaler` to the pinned local DLL. The middle command runs in WSL with Python 3 and libLLVM 15 installed. All extracted programs and generated shaders stay under `out/`.

```powershell
./out/pe-env/Scripts/python.exe -B tools/ffx_dispatch_profile.py $upscaler out/ffx-dispatch
wsl -d Ubuntu-22.04 -- python3 -B tools/ffx_dispatch_aot.py out/ffx-dispatch --llvm libLLVM-15.so.1
cmake --preset dev -B out/build/ffx-device "-DFFX_UPSCALER_IMAGE=$upscaler" "-DFFX_DISPATCH_SHADER_DIR=$PWD/out/ffx-dispatch"
cmake --build out/build/ffx-device --target offscreen_check
ctest --test-dir out/build/ffx-device -R '^ffx_provider_loop_' --no-tests=error -V
```

The local log reports `dispatch=package-aot`. Public Windows CI runs source-built descriptor and dispatch controls on WARP (`dispatch=desktop-control`). Linux CI assembles an independent integer-coordinate regression for the translator.

### First pass 26 LDS write block

At `0x2410`, the existing 8×4 dispatch skips to `0x26d8`. An optional D3D11 probe models wave zero of group (0,0) with 32 synchronized threads. It decodes the branch predicate and the first block's store addresses from the pinned producer. Distinct synthetic values identify each row, lane and component after the colour transform.

| Input | X scale (Y = 1) | Active lanes | Footprint | Tagged words |
| --- | ---: | ---: | --- | ---: |
| 32×4 | 243/256 | 32 | 32×6 | 576 |
| 32×4 | 244/256 | 32 | 33×6 | 0 |
| 31×4 | 243/256 | 30 | Unspecified | 0 |

On Windows build 26300.9974, NVIDIA GeForce RTX 4060 (610.88) and Microsoft WARP each matched an independent CPU predicate for 120 inputs. An inclusive horizontal limit changed only the 244/256 boundary case at 32×4 and unit vertical scale. All 576 shared-memory words matched their expected order or zero fill for each of the three rows on both adapters. D3D11 debug layer disabled. This probe covers the first gate and tagged stores. The production translator still skips the block. Later LDS reads and pixel output on this path remain unverified.

The Windows build compiles the readback host. The default CTest uses a source-owned HLSL fixture on WARP, exercising the same gate cases, inclusive-limit negative control and tagged stores against the CPU expectations. From the repository root, after configuring the dev preset:

```powershell
cmake --build --preset dev --target ffx_pass26_lds
ctest --preset dev --no-tests=error -R '^ffx_pass26_lds_warp$' -V
```

The physical adapter and pinned package are independent opt-ins. Use the producer generated by the dispatch profile above and a trusted LLVM 15 installation for the package comparison. Omit `--package` to run the source-owned input, or use `--backend hardware` for either input.

```powershell
out/pe-env/Scripts/python.exe -B tests/ffx_pass26_lds/verify.py --host out/build/dev/ffx_pass26_lds.exe --backend hardware
out/pe-env/Scripts/python.exe -B tests/ffx_pass26_lds/verify.py --host out/build/dev/ffx_pass26_lds.exe --backend warp --package out/ffx-dispatch/producer.bin C:/LLVM15/bin/LLVM-C.dll
```

### Last writers of the pass 26 scratch reads

The default FSR4-i8 graph with tier zero, unit render/output ratio and sharpening enabled schedules passes 24 and 25 immediately before pass 26. At provider RVA `0x136a7` the inclusion mask has bit 25 set. Other scheduling modes can skip the padding job. In the 8×4 case the host rounds both dimensions up to multiples of eight at `0x11f94`. Tensor 13 uses half that aligned size (4×4). The dispatch builder at `0x7e1c` and its padding helper at `0x7730` yield grids of 1×4×1 and 2×1×1 groups respectively.

Both kernels bind the provider's raw scratch UAV at `u11` (resource ID 22, index 12). Pass 24 performs its i8 convolution and stores 16 bytes at shader PC `0x218c`. With plane base `0x7f4bc0`, row stride `0x3c20` and 16 bytes per column, its output starts at column 1, row 1. The launched 64-wide groups also write beyond the four active columns. Pass 25's store at shader PC `0x200` writes four zero DWORDs. Its 44 active lanes cover the top and bottom rows, the left column and five columns of right padding around the 4×4 interior.

Pass 26 has eight active lanes with cell coordinates `(x,y)` in `[0,4) × [0,2)`. Its nine loads read at `base + (y+dy)*stride + 16*(x+dx)`. For a given address, interior columns 1–4 on rows 1–3 of the read window have pass 24 as last writer. All other addresses in that window have pass 25 as last writer.

| Pass 26 shader PC | (dx, dy) | Lanes reading pass 24 | Lanes reading pass 25 |
|---|---|---:|---:|
| `0x14c` | (0, 0) | 3 | 5 |
| `0x154` | (1, 0) | 4 | 4 |
| `0x15c` | (2, 0) | 3 | 5 |
| `0x164` | (0, 1) | 6 | 2 |
| `0x16c` | (1, 1) | 8 | 0 |
| `0x174` | (2, 1) | 6 | 2 |
| `0x17c` | (0, 2) | 6 | 2 |
| `0x184` | (1, 2) | 8 | 0 |
| `0x18c` | (2, 2) | 6 | 2 |

The reproduction commands above also translate the two upstream shaders and use four 64-byte constant ranges from the pinned model initializer. The test seeds the pass 24 input with either zero or a deterministic pattern, fills the output region with a sentinel, and uses the same GPU buffer across all three passes. It copies the 6×4 read window after each writer and records the address and all four DWORDs at each of the 72 pass 26 loads. A third control bypasses both writers and supplies zero scratch directly to pass 26. Model biases can produce nonzero activations even from a zero upstream input.

A separate Python evaluator checks the scratch words and the four history-blend logits per lane using signed-byte dot products. Spatially constant, distinct scene and history colors give a per-pixel blend reference for resource 16. The existing five-tap FP32 reference checks all 256 final RCAS pixels, including the surrounding pattern. Switching the upstream input changed all 32 active pixels at both stages on RTX 4060 Laptop, native RGB9E5 WARP and forced R32_UINT WARP. Both adapters reported zero debug warnings and errors. The tensor feeding pass 24 remains a fixture; the full-wave LDS branch of pass 26 is a separate open path.

### FidelityFX context creation and ownership

The Windows test calls the translated loader create function, then passes its returned handle directly to the existing destroy translation. Create binds a source-owned upscaler provider to the two table slots. On success, the loader allocates a 16-byte wrapper and stores the table pointer and provider context in it. A provider create error propagates its 32-bit return code, clears the caller's handle and skips wrapper allocation. Destroy calls the provider with the address of the inner field before releasing the wrapper, including on a provider destroy error.

Fourteen cases cover default CRT allocation and custom allocation with user data. For each route the test checks success, two destroy errors, three create errors, and failure to allocate the wrapper after the provider succeeds. It observes allocation and release addresses, order, sizes and the live-allocation count. In the allocation-failure case the host adapter calls provider destroy once and returns memory error 5. This rollback is a Windows adapter behavior. The inspected loader proceeds to a wrapper store after its allocator returns. The provider used in this comparison owns real CRT memory and has controlled return codes; creation of a live upscaling effect on a graphics device remains open.

```powershell
cmake --build --preset dev --target cpu_create_lifted cpu_destroy_lifted
ctest --preset dev --no-tests=error -R '^cpu_aot_context_lifecycle$' -V
```

CI compares an independently assembled native create caller and its translation, each paired with the fixture's lifted destroy. To check the package translation from a VS x64 developer shell, set `$loader` to the path of the local FidelityFX loader and run:

```powershell
New-Item -ItemType Directory -Force out/ffx-lifecycle | Out-Null
./out/pe-env/Scripts/python.exe -B tools/ffx_create_context.py $loader out/ffx-lifecycle/create.cpp
./out/pe-env/Scripts/python.exe -B tools/cpu_chained_aot.py $loader ffxDestroyContext out/ffx-lifecycle/destroy.cpp --ffx-destroy
foreach ($unit in 'create','destroy') {
    $prefix = "out/ffx-lifecycle/$unit"
    cl /nologo /O2 /EHsc /LD "/Fo:$prefix.obj" "$prefix.cpp" /link "/OUT:$prefix.dll" "/IMPLIB:$prefix.lib"
    if ($LASTEXITCODE) { throw "Compilation failed for $unit" }
}
./out/pe-env/Scripts/python.exe -B tests/cpu/context_lifecycle.py out/build/dev/cpu_create.dll out/ffx-lifecycle/create.dll out/ffx-lifecycle/destroy.dll
```

### FidelityFX version enumeration

The Windows query returned one upscaler, version ID `0xf5a5ca1e01001001` and name `4.1.1`. The first call requested the count. The second supplied one ID slot and one name slot. Both returned status 0. The descriptor uses AMD's null-context, type-4 version query. [AMD FidelityFX query descriptor](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/main/Kits/FidelityFX/api/include/ffx_api.h)

`tools/ffx_version_query.py` checks both input DLL hashes, decodes the selected x64 query intervals and the provider's two filter functions, and emits their integer operations and branches as C++. The generated Windows DLL combines both modules' selected paths. Its host binding specializes module resolution to one available upscaler and no frame-generation module. It reconstructs the single provider entry from the initializer's stores, including the full ID and the referenced name. Static storage keeps the returned name valid for the lifetime of the DLL.

On this build, capacity zero reports the available count without writing array elements. ID-only and name-only calls both succeed. Capacity two writes one result and leaves the second slot untouched. A missing frame-generation module returns 4 and an unknown effect returns 2. In both cases the loader clears the count **pointer in the descriptor** and leaves the caller's count value untouched. This entry point covers version enumeration only.

Run these commands in an x64 VS developer shell with the Python development environment available. Set `$loader` and `$upscaler` to the two local input paths.

```powershell
New-Item -ItemType Directory -Force out/ffx-query | Out-Null
./out/pe-env/Scripts/python.exe -B tools/ffx_version_query.py $loader $upscaler out/ffx-query/query.cpp
cl /nologo /O2 /EHsc /LD /Fo:out/ffx-query/query.obj out/ffx-query/query.cpp /link /OUT:out/ffx-query/query.dll /IMPLIB:out/ffx-query/query.lib
./out/pe-env/Scripts/python.exe -B tests/cpu/version_query_check.py out/ffx-query/query.dll query_versions 0xf5a5ca1e01001001 4.1.1
```

The public CTest `cpu_aot_version_query` runs the same output checks against an independent MASM fixture and its translation. The fixture uses a different name and 64-bit ID. The two proprietary inputs and generated code stay local.

## Recompiling a direct x64 call

The [caller and callee](../tests/cpu/direct.asm) each accept two unsigned 64-bit integers and an output pointer. The [translator](../tools/cpu_call_aot.py) starts at the caller export, follows the signed displacement in its E8 call and reads the target's runtime-function interval. It generates two C++ functions from the linked machine code. MSVC emits a separate x64 DLL with a non-inlined call. The Windows test checks the relative call against the two export addresses in both the input and output DLLs.

```powershell
cmake --build --preset dev --target cpu_call_original_lifted cpu_call_altered_lifted cpu_register_probe
ctest --preset dev --no-tests=error -R '^cpu_aot_direct_call$' -V
```

The caller changes both arguments and keeps its original inputs live across the call. The callee records its arguments and arithmetic result through R8. The caller uses the returned RAX and both live inputs to compute its result. A separate assembly probe seeds physical RBX and RSI and reads them immediately after each invocation. Tests exercise the whole call and the callee in isolation using the same 249 input pairs, including wraparound and high-word differences. They compare the return, three output words, adjacent guards and both register sentinels with the original and independent arithmetic.

A rebuilt callee that increments only its return after storing the result exposes a mismatch for every pair. A register-clobber control checks the probe itself. Targets redirected into the middle of the callee or back to the caller are rejected during translation. Coverage is limited to this integer ABI, one direct call, its two fixed frame layouts and the decoded register operations. [Windows x64 calling convention](https://learn.microsoft.com/en-us/cpp/build/x64-calling-convention) · [Import-call follow-up](https://github.com/R0Y00/Xbox2Windows/issues/24)

## Calling another DLL through a named PE import

The source-built caller uses an x64 RIP-relative indirect call (`FF 15 disp32`). The translator computes the import address table (IAT) slot RVA from the end of the instruction and looks up the exact slot in the ordinary import descriptors. The build supplies an explicit binding for the fixture's integer ABI. The DLL name and case-sensitive symbol must match the slot, and the provider must have a local executable export for that name. MSVC compiles the generated caller and links the provider's import library. The test independently checks that the call in each linked caller addresses its own slot for `cpu_import_provider.dll!mix_args`. [PE import address table](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format#import-address-table)

```powershell
cmake --build --preset dev --target cpu_import_original_lifted cpu_import_altered cpu_register_probe
ctest --preset dev --no-tests=error -R '^cpu_aot_import_call$' -V
```

Each provider configuration runs in a fresh Windows process. The provider records the two arguments it received and its arithmetic result in three output words. For 249 pairs the test compares both callers with independent arithmetic, checks the adjacent guard words and samples RBX and RSI. Replacing the provider with one that increments its return changes the caller's result on every pair and preserves the output writes.

With the provider absent, both callers fail to load with error 126. With a DLL of the expected name that lacks the requested export, both fail with error 127. The translator also diagnoses the missing provider or export before compilation and rejects a call displaced one byte into the IAT slot. The verified profile covers one ordinary named import with two 64-bit integers and an output pointer. [Ordinal-only import follow-up](https://github.com/R0Y00/Xbox2Windows/issues/26)

## Calling an ordinal-only export

The source-built provider exposes only ordinal 77 through a NONAME export. The build binds `#77` with the local import-library symbol `mix_args`. The translator checks the DLL and ordinal from the call's IAT slot against the actual export address table. The test reads both linked callers' import lookup words, checks the ordinal flag and value, and verifies the provider has no exported name for the entry.

```powershell
cmake --build --preset dev --target cpu_ordinal_original_lifted cpu_ordinal_altered cpu_register_probe cpu_import_provider
ctest --preset dev --no-tests=error -R '^cpu_aot_ordinal_import$' -V
```

For 249 input pairs both callers match the provider's observed arguments, guarded writes, register sentinels and expected return. Replacing the provider changes the return on all inputs. Both callers fail to load with error 126 when the DLL is absent and error 182 when the DLL exports the same link name but lacks ordinal 77. The static translator diagnoses a mismatched binding, a missing link name, a missing ordinal and a displacement into the middle of a slot. When the provider is renamed `ws2_32.dll`, pefile names ordinal 77 from its built-in table. The translator still requires the `#77` binding and rejects that name.

The local Bink DLL imports ordinal 1 from XAudio2_9.dll. A read-only check confirmed an executable export at that ordinal in this workstation's System32 provider. The Bink call's argument and result contract remains unverified.

## Mapping an image without LoadLibrary

`tools/pe_map.cpp` maps an x64 PE32+ image into memory it allocates itself, away from the preferred base, and applies its DIR64 base relocations. It binds ordinary and delay imports through a resolver supplied by the host, sets section protections, records the actual base in the mapped headers and registers `.pdata` with `RtlAddFunctionTable`. The original machine code runs unchanged. Header fields are checked against the file and image size before use. It then calls TLS callbacks and the entry point. TLS data templates are rejected. [Issue #46](https://github.com/R0Y00/Xbox2Windows/issues/46)

```powershell
cmake --build --preset dev --target pe_map cpu_reloc_original cpu_import_original_lifted cpu_ordinal_original_lifted cpu_import_altered cpu_ordinal_altered cpu_register_probe cpu_two_imports
ctest --preset dev --no-tests=error -R '^cpu_native_map$' -V
```

The test maps the named and ordinal import fixtures, `reloc_route` and a damaged copy of the register probe. In `reloc_route`, `call_mix` reaches the provider through an absolute code pointer in `.data`, which gives the image exactly one DIR64 relocation; the test checks that the mapped pointer equals its linked value plus the load delta. The two import fixtures have no relocation entries. For every image the test checks the following:

- the base differs from the preferred base and is recorded in the mapped headers;
- the IAT slot holds the export the resolver returned;
- `RtlLookupFunctionEntry` finds `call_mix` in the registered table until the image is released.

The mapped `call_mix` is compared over 249 input pairs on arguments, guarded writes, RBX/RSI sentinels and return value. It matches the reference and the same image loaded by Windows, and for the import fixtures it also matches the lifted DLL. If the import is bound to the altered provider instead of the linked one, only the return changes. An unbound import is rejected with its DLL and symbol or ordinal. Copies patched to have an entry point outside any executable section or an import directory that runs past the image are rejected before any import is requested. Like the Windows loader, `pe_map` finds the import table by its address and walks it to the null descriptor without using the directory `Size`; each descriptor is bounds-checked against the image. `two_imports` imports `mix_args` from the named provider and `probe` from the register probe. With its import directory `Size` left at 60 or patched to 0, 19 or 20 (ending after the first descriptor), `LoadLibraryExW` and `pe_map` both bind both IAT slots; the test only reads the slots and runs nothing in the image. The Windows loader does not read the delay-import directory at all: copies of the named fixture with an empty table, an address of 0, an address past the image or a descriptor naming a DLL all load. `pe_map` maps the first two, rejects a directory outside the image as a format error and one that lists a DLL as unsupported. In a copy of the register probe with the `clobber_control` export address slot zeroed, that name no longer resolves while `probe` still does.

`cpu_native_seh` checks exception dispatch through a mapped image. In `seh_route`, `guarded_load(address, mode, trace)` names its own exception handler in its unwind data. The handler resumes an access violation raised by the load instruction at a recovery label with a fixed value in RAX, and declines every other exception. The cases run in one child process for the Windows-loaded original and another for the mapped copy, so a broken unwind crashes only the child and is reported as a failed check:

- a valid load returns the value;
- a load from address zero is caught by the image's own handler and returns the fixed value, with RBX and RSI restored;
- an illegal instruction, reached after the first trace write, is declined and unwinds through the mapped frame to the ctypes caller, which reports exception code `0xc000001d`;
- a later valid call still succeeds.

```powershell
ctest --preset dev --no-tests=error -R '^cpu_native_seh$' -V
```

`cpu_native_section` compares every section of the register probe after `LoadLibraryExW` and after `pe_map`, byte for byte, on the original and on copies with one section header patched. The probe has no entry point and no imports, so the Windows load runs none of its code. On Windows 11 build 26300.9974, the loader reads section data in whole `0x200`-byte file sectors: it rounds `PointerToRawData` down and the end of the raw data, `PointerToRawData + SizeOfRawData`, up to a multiple of `0x200`. It copies that range up to the section's virtual size, and bytes past the end of the file stay zero. `pe_map` now reads the same range. The test covers a start inside a sector, a start in the first sector, which reads the headers from offset 0, a raw size below one sector and raw data that crosses a sector boundary. In each case Windows differs from an exact read of `SizeOfRawData` bytes at `PointerToRawData`. A last case shortens the file so that the rounded end of `.rsrc` lies past it.

A `PointerToRawData` of zero is the exception: Windows leaves that section zero-filled even when `SizeOfRawData` is set, and so does `pe_map`. The rounding cases patch `.rdata`, because in `.rsrc` a rounded start can put other data where the resource directory should be, and `LoadLibraryExW` then fails with `ERROR_BAD_EXE_FORMAT`. These results were measured with a `FileAlignment` of `0x200` and `0x400`. With `0x1000`, Windows filled more of a section whose raw size is below one sector than the rounded sector end allows, which remains open.

```powershell
ctest --preset dev --no-tests=error -R '^cpu_native_section$' -V
```


### TLS callbacks and the entry point

`tls_route` has an entry point and a TLS directory whose callback list holds one routine and whose data template is empty. Both routines report each call to `cpu_tls_sink.dll`, which outlives the image, so calls made during release stay visible. `cpu_native_tls` loads the image with `LoadLibraryW` and maps it with `pe_map`, each in its own child process, and compares the process notifications:

- Attach: with both loaders the TLS callback runs before the entry point, each with `DLL_PROCESS_ATTACH`, the module base and a null reserved argument.
- Detach: on Windows 11 build 26300, `FreeLibrary` called the TLS callback first and then the entry point, both with `DLL_PROCESS_DETACH`. `pe_map_release` follows that measured order. No Microsoft documentation specifies it.
- Failed attach: in `cpu_tls_refuse` the entry point returns FALSE. Windows then sends a full detach in the same order, and `LoadLibraryW` fails with error 1114. In the same case `pe_map` sends the same detach and returns `MAP_INIT_FAILED`.

Like the Windows loader, `pe_map` finds the TLS directory by its address and does not use its `Size`. On Windows 11 build 26200, copies with `Size` 0, 1 or 39 still run the callback before the entry point, under both loaders; with the address set to 0 only the entry point runs; a directory past the end of the image fails `LoadLibrary` with error 998 and `pe_map` with "TLS directory outside image". Between attach and release the test starts and joins one thread whose start routine is `Sleep`. On that thread both loaders call the TLS callback and then the entry point with `DLL_THREAD_ATTACH`, then the same pair with `DLL_THREAD_DETACH`, all with a null reserved argument. `pe_map.dll` is itself loaded by Windows, and its `DllMain` forwards these notifications to every mapped image: attach in mapping order and detach in reverse, which is what two `LoadLibraryW` copies receive on Windows 11 build 26300. The sink records the calling thread, so notifications from unrelated threads in the process are ignored. A thread created between an image's process attach and its registration receives the detach without the attach, as a thread that existed before mapping does. Each mapped image holds one reference for its registration and one per thread notification in flight. `DllMain` takes those references under the registry lock and calls the guest callbacks after releasing it, so a callback may map or release any image, its own included. `pe_map_release` unregisters the image and returns 1 at once. The image's process detach and unmapping run when its last reference goes, inside `pe_map_release` or on the thread that finishes the last notification, and happen exactly once. No notification that starts after a release reaches the image; one already in progress finishes, so an image whose TLS callback releases it still has its entry point called for that thread attach. `cpu_native_lifetime` compares the two-image order with two native loads, and releases an image from inside a thread-attach callback, both another image and the image itself, through a hook armed for that thread only. A release from another thread while a callback is running is not covered by a test. A second release returns 0. Damaged copies are rejected before any notification: one with a nonzero `SizeOfZeroFill` as TLS data, one with the entry point moved into `.data`, and one with its TLS callback pointing into `.data`. The entry point and callbacks must lie in an executable section. The TLS directory and each callback slot are read after section protections are applied, so `pe_map` checks the live page protection with `VirtualQuery` before every such read, on attach and on release. In `tls_route` the callback list has its own read-only `.tlscb` section. Copies with `.data` or `.tlscb` made inaccessible are rejected as "TLS directory not readable" or "TLS callback list not readable". A list made `PAGE_NOACCESS` after mapping is skipped on release while the entry point is still detached. `pe_map_export` does not yet apply this check to the export tables. A list longer than 256 callbacks is rejected as unsupported. With `IMAGE_FILE_DLL` cleared, with or without an entry point, `LoadLibraryW` loads and frees the copy without calling the TLS callback or the entry point, and `pe_map` does the same. `pe_map` still applies the executable-section checks to such an image, where Windows would load it and call nothing. The callback list collected before attach is reused for the detach that follows a failed attach. The list is re-read on release; if it was overwritten with a non-executable address or made unreadable after mapping, only the entry point is detached. Implicit TLS data would need a slot in each thread's `ThreadLocalStoragePointer` array, which ntdll owns. `pe_map` therefore leaves `AddressOfIndex` as linked. In the test process, Windows wrote index 8 for this image even though its template is empty.

```powershell
ctest --preset dev --no-tests=error -R '^cpu_native_tls$' -V
```

### An empty relocation directory away from the preferred base

`cpu_import_original` contains no absolute addresses. Its base-relocation directory has Size 0 and `IMAGE_FILE_RELOCS_STRIPPED` is clear. The issue used `cpu_register_probe`, which has the same properties; this test uses `cpu_import_original` because it exports `call_mix`, so a call can be checked after loading, and the register probe is the harness.

`cpu_native_reloc_free` runs each case in its own child process. It reserves the preferred base (`0x180000000`) for `SizeOfImage` and checks with `VirtualQuery` that the whole range is held by that reservation. It then loads the image with `LoadLibraryExW` and maps it with `pe_map`. A second copy has `IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE` cleared.

The discriminating case is Windows with the `DYNAMIC_BASE`-cleared copy. With nothing reserved, Windows loads that copy at `0x180000000`. With the base reserved, it loads it elsewhere and `call_mix` matches the reference on all 249 input pairs. The as-built copy also moves under Windows, but ASLR would move it anyway. `pe_map` always allocates away from `ImageBase`, so for it the reservation only shows that an empty relocation directory is accepted at another base and that the image runs there.

The bases vary between runs, so the test checks only that they differ from the preferred base. With the base reserved, a copy that sets `IMAGE_FILE_RELOCS_STRIPPED` fails in `LoadLibraryExW` with error 487 (`ERROR_INVALID_ADDRESS`) and is rejected by `pe_map` as "relocations stripped"; `pe_map` would reject it with or without the reservation.

`pe_map` therefore does not reject an empty relocation directory when it maps away from `ImageBase`, because Windows loads such images. An image that contains absolute addresses but omits its relocations is malformed and would fail at run time under either loader. Measured on Windows 11 build 26300. [Issue #77](https://github.com/R0Y00/Xbox2Windows/issues/77)

```powershell
cmake --build --preset dev --target pe_map cpu_import_original cpu_import_provider cpu_register_probe
ctest --preset dev --no-tests=error -R '^cpu_native_reloc_free$' -V
```

## Switch dispatch through jump tables

Compilers lower a dense `switch` to a bounds check and an indirect `JMP` through a table of 32-bit entries. The [jump-table translator](../tools/cpu_switch_aot.py) recognizes the two x64 layouts in common use. MSVC stores image-relative entries and loads them through a register holding `__ImageBase`. LLVM stores signed offsets from the table and adds the table address. The translator reads the table from the PE, then emits a C++ `switch` whose cases jump to labels at the decoded targets. The host compiler builds its own dispatch from that switch.

```powershell
cmake --build --preset dev --target cpu_switch_original_lifted cpu_switch_swapped_lifted cpu_register_probe
ctest --preset dev --no-tests=error -R '^cpu_aot_jump_tables$' -V
```

The decoder starts at the export and follows branches, fallthrough and table entries, so it never decodes the table bytes that MSVC places after a function's code. An indirect jump is accepted only in the sequence `load entry; add base; jmp`, reached by the fallthrough of an unsigned `JA` or `JAE` against an immediate. The table length comes from that bound. A must-agree constant pass over the control-flow graph gives the base register's RIP-relative value, which also covers a base loaded once outside a loop. Value numbering over the single-entry block that contains the bound shows that the index register holds the compared value, zero- or sign-extended from 32 bits. Every table entry must be the first byte of a decoded instruction inside the function, and the table must not overlap any decoded instruction. Flags are modelled only between a producer and the conditional jump that immediately follows it. A forward pass rejects any read of a register that some path leaves undefined. Writes to nonvolatile registers, stack operations, calls and partial-register writes are rejected.

The supported functions take three integer words in RCX, RDX and R8 and return RAX. A leaf function without a `.pdata` entry is bounded by its executable section. Its walk may not enter an interval owned by another function's unwind entry.

The [source fixture](../tests/cpu/switch.asm) exports four functions. `select_rva` uses the MSVC layout, and `select_rel` computes the same function through the LLVM layout. Both have seven cases, including two selectors that share a target, a case that falls through into the next, and a default path. `run_ops` runs a byte program in a loop and dispatches every byte through a five-entry table. It stops at the first opcode above 4. Through the register probe, the test compares each original export with the matching lifted function, a reference written in Python, and RBX/RSI sentinels. It runs 600 selector and operand pairs per layout and 138 programs. Half of the pairs carry nonzero upper register halves that must not reach the table index. A control assembles the same source with two entries swapped in each table, and its translation must follow the swapped reference. `shift_narrow` dispatches through a six-entry LLVM-layout table to byte and word `SHL`, `SHR` and `SAR` of a memory destination by `CL`. x86 masks the count to five bits for 8- and 16-bit operands as well, so counts of 8 to 31 still shift (a byte holding 1 shifted left by 8 becomes 0). The translator performs these shifts in 64 bits and truncates the stored result; 4,608 inputs, covering every count from 0 to 31 and count bytes above 31, match the original and the reference.

On a copy of the linked DLL, the test patches the code, the table or `.pdata`, and expects each change below to be rejected:

- the bound jump removed or made signed;
- a sign extension that no longer comes from the compared register;
- a table entry into an instruction interior or into the neighbouring function;
- a table placed over code, or a second entry into the dispatch block;
- a base that is not the image base, or that does not come from a RIP-relative `LEA`;
- an undefined or nonvolatile register, or an unsupported instruction;
- a leaf whose walk reaches another function's unwind interval.

When `.pdata` no longer covers `select_rva`, the translation must stay unchanged.

On Linux, the fixture was assembled and linked with LLVM (`llvm-mc`, `lld-link`) from an equivalent GNU-syntax copy, because `llvm-ml` does not accept `IMAGEREL`. The original machine code ran in-process through an `ms_abi` call. It matched the lifted C++ built with clang on every input, for both the original and the swapped tables. The same translator also handled C switch statements compiled by clang for `x86_64-pc-windows-msvc` at `-O1` and `-O2`. Those builds have no `.pdata` for the leaves, and the loop's table base is hoisted. The generated sources compile without warnings under clang-cl `/W4 /WX`. On Windows 11 with MSVC 14.44 and ML64, `cpu_aot_jump_tables` passes: 600 selector pairs per table layout and 138 programs match, the swapped tables change 60, 60 and 99 results, and all 13 rejection controls and the leaf-without-unwind translation pass. The table labels are declared with `LABEL DWORD`, because ML64 rejects `IMAGEREL` of a code label inside a memory operand.

### Delay imports

`delay_route` imports `delayed_value` from `cpu_delay_provider.dll` by name and `delayed_ordinal` by ordinal 2 with `/DELAYLOAD`, and exports one caller for each. `cpu_native_delay` compares two paths: `LoadLibrary` plus the image's own delay helper, and `pe_map` with a resolver bound to the same provider. The mapped calls return the provider values, and IAT slot checks confirm both hold the `GetProcAddress` results before the calls. Binding the altered provider returns its values instead.

Windows leaves a delay IAT slot pointing at its thunk until the first call. `pe_map` resolves every delay descriptor at map time because it has no `LoadLibrary`, and the image's helper would bypass the host resolver. An absent provider therefore fails the map with `MAP_UNRESOLVED_IMPORT`, where Windows would map and fail on the first call. Descriptors must set `dlattrRva`; a descriptor without it stores virtual addresses and is rejected as unsupported.

Like the TLS and ordinary import tables, the delay-import directory is found by its address and `Size` is not used. The table is walked to the null descriptor; a copy with `Size` zero still binds, and a directory address of zero ignores a nonzero `Size`. Copies patched to clear `dlattrRva`, point the DLL name outside the image, zero the name table, point the IAT outside the image, or move the directory address outside the image are rejected before the provider is requested.

```powershell
ctest --preset dev --no-tests=error -R '^cpu_native_delay$' -V
```

## Offline pixel shader recompilation

`tools/pixel_aot.py` extracts one named pixel program from a local archive, decodes gfx1013 instructions with LLVM and emits HLSL. DXC compiles the result to desktop DXIL before the draw. The lowerer tracks register writes across whole-quad interpolation of attribute 0, a scalar texture-descriptor load, a 2D RGBA sample, packed round-toward-zero binary16 conversions and one compressed color export. Unknown instructions, mismatched bindings and reads before the required wait cause translation to fail. [LLVM disassembler API](https://llvm.org/docs/doxygen/group__LLVMCDisassembler.html) · [Leonida public format reference](https://github.com/ItsNotRexed/Leonida/blob/56f023a10a726da20702d4e121656ceda2391d93/tools/fssm_unpack.py)

The caller identifies the entry registers for the texture address and interpolation base, and supplies the four sampler descriptor words to match. The generated pixel shader uses `TEXCOORD0`, texture `t0` and sampler `s0`. The test binds a point-clamp sampler, one mip and a full-screen triangle. These bindings are an explicit specialization. The test measures finite UNORM inputs under full pixel coverage. Execution with a title's original pipeline state, helper-lane behavior at triangle edges and other texture formats require separate validation.

For a shader that reads constants, pass `--constant-descriptor OFFSET` to identify its four-word descriptor in the same table as the texture descriptor. The desktop test binds 256 bytes at `b0`. A four-word read may span two HLSL constant vectors. Arithmetic support includes log2, exp2, reciprocal, min/max, subtraction, legacy zero-preserving multiplication, multiply-add, destination accumulation and saturation. Ordered and unordered `v_cmp_*_f32` comparisons, including the always-false and always-true predicates, but excluding class and exec-writing forms, become HLSL booleans, and `v_cndmask_b32` selects between two numeric operands with one. The lowerer accepts a mask in `vcc` or a scalar register under either wavefront-width spelling and rejects a select whose mask it did not see a comparison write. The unordered forms are emitted as negations so that strict compilation can keep them true for NaN operands. The pixel draw checks cover all 16 truth tables with less-than, signed-zero equality, greater-than and a quiet NaN on either side. Replacing a NaN with an equal finite operand must fail against the saved unordered reference. Compile arithmetic shaders with DXC `-Gis`. Transcendental precision and multiply-add rounding may vary by device. [AMD RDNA instruction reference](https://gpuopen.com/wp-content/uploads/2019/08/RDNA_Shader_ISA_5August2019.pdf) · [HLSL mad precision](https://learn.microsoft.com/en-us/windows/win32/direct3dhlsl/mad)

From the repository root on Linux with LLVM 15 installed, assemble the original test shaders, wrap them in synthetic records, then extract, decode and translate them. The test compares the result with lowering the source operands directly.

```sh
python3 -B tests/pixel_aot_check.py binary llvm-mc-15 llvm-objcopy-15 libLLVM-15.so.1
```

On Windows, use the Python-enabled build from the project README. Each backend draws a copy, a red/blue swap, a nonlinear curve and a compare-and-select program, the last two with two sets of constants each. It compares all four channels of 4,096 pixels bitwise with an independent CPU reference. Negative controls change the expected channel order, the bound gain or the comparison threshold and must report a mismatch. The select program uses only comparisons, one subtraction and selects, so its reference contains no transcendental or fused step and assumes only correctly rounded UNORM sampling. Its inputs are finite, so NaN operands remain unmeasured. The absolute-value fixture also draws abs(0.5 - red) and checks that removing the modifier produces a mismatch.

To supply a reference for another arithmetic program, run `offscreen_check.exe hardware --check-pixel-case shader.cso reference.case`. The case contains 256 bytes of constants followed by 64 expected RGBA colors in row-major little-endian binary32, one for each texel of the test texture. Generate the expected colors after round-toward-zero half export and widening back to float.

```powershell
ctest --preset dev -R '^pixel_aot_' -V
```

For a compatible local record, substitute the archive, exact record name, two register numbers and four descriptor words below. Run the translator from the repository root in WSL, then compile and draw from a Windows development shell. The JSON printed by the translator identifies each input and output by SHA-256 for local reproduction. The renderer's `rgba` expectation checks a copy and `bgra` checks a red/blue swap.

```sh
python3 -B tools/pixel_aot.py "$archive" "$record" --llvm libLLVM-15.so.1 \
    --texture-sgpr "$texture_reg" --interp-sgpr "$interp_reg" \
    --sampler-words "$word0" "$word1" "$word2" "$word3" --output out/pixel-aot/translated.hlsl
```

```powershell
.\tools\dev.ps1
dxc -T ps_6_0 -E main -Fo out/pixel-aot/translated.cso out/pixel-aot/translated.hlsl
.\out\build\dev\offscreen_check.exe hardware --check-pixel out/pixel-aot/translated.cso rgba
.\out\build\dev\offscreen_check.exe warp --check-pixel out/pixel-aot/translated.cso rgba
```

## Package integrity

For a local build with a multi-file v1 torrent and `layout*.xml`, run the offline verification with Python 3.9 or newer. Substitute your local directory name.

```powershell
python tools/verify_build.py "game-files/<build-directory>"
```

The script verifies SHA-1 pieces across file boundaries in torrent order and compares layout destinations separately with disk and torrent entries. The report is `out/integrity/<build-directory>.json`. Exit code 0 means the payload passed piece verification. `layout.absent_from_disk` lists missing layout entries separately.

## PE dependency baseline

The scanner uses x64 Windows, Python 3.11 or newer and pefile. Set up an isolated environment once.

```powershell
python -m venv out/pe-env
.\out\pe-env\Scripts\python.exe -m pip install pefile==2024.8.26
```

Capture a DLL-only baseline. When an EXE becomes available, write a separate report and compare it with the saved baseline.

```powershell
$pePython = '.\out\pe-env\Scripts\python.exe'
$buildDir = 'game-files/<build-directory>'
& $pePython tools/pe_inventory.py $buildDir --output out/dependencies/pre-exe.json
# Run the next command after adding the main executable.
& $pePython tools/pe_inventory.py $buildDir --baseline out/dependencies/pre-exe.json --output out/dependencies/with-exe.json
```

`summary` gives counts. `unresolved_edges` lists requested exports missing from both the package and the host. Each `imports` entry identifies the caller, import mechanism, provider and symbol. `#N` denotes an ordinal. The scanner reads import descriptors and lookup tables itself. pefile skips names outside letters, digits and `._?@$()<>` and drops a descriptor left empty, so those entries would otherwise vanish from the graph. Ordinal imports stay `#N` even where pefile's built-in table supplies a name. `images` includes architecture, SHA-256, export names, ordinals and forwarders. `diff` distinguishes new caller relationships from dependencies seen for the first time in the build.

The scanner reads System32 export tables and applies the host API-set v6 map at each hop, using the forwarding DLL as the importer at the next hop. Each import's `desktop_resolution.chain` records the requested module, selected provider, symbol and any advertised forwarder. Its final status identifies a terminal export, missing provider or export, malformed forwarder, or cycle. `desktop_export` retains the first-hop result for older report consumers. The comparison covers System32 DLLs on the measured machine. Package selection at runtime, parameter layouts and behavior require separate evidence. [PE export address table](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format#export-address-table) · [Wine API-set lookup](https://github.com/wine-mirror/wine/blob/master/dlls/ntdll/loader.c)

Package DLLs can also forward exports, to another package DLL, to themselves, by ordinal or to System32. An edge whose provider is in the package gets a `package_resolution` chain. It uses the same statuses, adds `ambiguous_package_basename`, and marks each hop's `package` origin. Plain module names select a uniquely named package image before System32. API-set contracts always select System32 hosts. KnownDLLs, which take precedence over the application directory, are not modeled. An edge counts as resolved through the package only if its chain reaches a terminal export. `dependency_paths` adds each packaged forwarder target after its provider with the reason `forwarder` and reports a broken chain under the chain's terminal status. Earlier tools treated any matching export name as resolved. They omitted the target module and accepted forwarders to missing exports, missing modules or cycles. The `bundled_export_forwarders` test builds `intake_forward.dll` with six placeholder exports, because linkers differ in how a .def file declares forwarders. It moves the export table of a temporary copy into a new section and turns the six exports into forwarders: by name, by ordinal, a two-hop chain through itself, a missing target export, a self-cycle and an absent module. It then checks the inventory and path report for a caller that imports all six.

On Windows build 26200, the pre-executable inventory covered 16 package DLLs and 1,337 import edges. Of these, 171 edges (78 distinct requested module/symbol pairs) went through a single export forwarder. All 171 reached a terminal export. In total 1,329 edges resolved on the desktop. The eight remaining edges requested six distinct interfaces across `xmem.dll` and `d3d12_xs.dll`. The 16 package DLLs had zero forwarded exports of their own. The historical baseline was preserved for comparison.

## Heap and process memory

`src/era_heap.cpp` is a desktop handler. It splits the attribute word using SlimEra's Xbox One ERA layout: object type in bits 0–13, page size in bits 14–15, allocator ID in bits 16–23, log2 alignment in bits 24–28, memory type in bits 29–32 and reserved bits 33–63. Object type and allocator ID are carried as tags and do not affect placement, as in SlimEra's default heap.

The desktop handling is:

- **Heap (memory type 0):** served from the CRT heap at the requested alignment, minimum 16 bytes, and zero-filled. The page size does not affect these requests.
- **Graphics (memory types 1–6):** committed as a separate `VirtualAlloc` region. `release_with_attributes` frees it with `VirtualFree`. The region starts on the desktop allocation granularity, which covers 4 KB and 64 KB pages. This is ordinary CPU-visible memory; no GPU mapping or caching behavior is modeled.
- **Rejected:** physical memory types 7–9, undefined types 10–15, the undefined page-size value 3, reserved bits, and graphics requests whose page size or alignment exceeds the allocation granularity all return a null pointer.

`release` frees heap allocations as before. `release_with_attributes` takes the allocation's attribute word, as `XMemFree` callers do.

What is measured is the desktop behavior above. Under the ERA layout, the two recorded words give alignments that match their callers' own arithmetic: 32 bytes for Bink and 256 bytes for AMD. The following remain unconfirmed on Scarlett:

- that Scarlett uses the ERA layout at all;
- what allocator ID `0xe5` means;
- the GPU visibility and caching of memory type 5 and the other graphics types;
- the console's error conventions.

[SlimEra attributes](https://github.com/XWine1/SlimEra/blob/main/era.h#L105-L137) · [SlimEra default heap](https://github.com/XWine1/SlimEra/blob/main/kernelx.cpp#L600-L657) · [Issue #126](https://github.com/R0Y00/Xbox2Windows/issues/126)

| CTest case | Inputs and checks |
|---|---|
| `era_heap_allocation` | Allocate 257 bytes using six alignment exponents. Check alignment of at least 16 bytes and the requested alignment, initial zeroing, writes and release. Debug builds also compare CRT heap snapshots. |
| `era_heap_failure` | Each reserved bit, combined with a heap alignment and with the AMD graphics word, returns a null pointer. So do memory types 7–15, page-size value 3, graphics memory with 4 MB pages or a 128 KiB alignment, zero size and excessive size. |
| `era_heap_reuse` | For three alignment exponents and three sizes, fill a block with `0xa5` and release it. Then hold up to 32 new blocks of the same shape until the released address returns. Every new block must read as zero. The output reports `recycled_address`, the number of the nine cases in which the CRT returned the dirty address. glibc returned all nine in a Linux build with a `posix_memalign` stand-in. Windows 11 build 26200.9974 with MSVC 14.44.35207 returned the dirty address in all nine cases in both Debug and Release. Low-fragmentation heap randomization can lower it. |
| `era_heap_observed_words` | Desktop handling of the two recorded words. Under the ERA layout, the Bink word `0x05e50000` splits into heap memory, 4 KB pages, allocator field `0xe5` and 32-byte alignment. The AMD word `0xa8004000` splits into memory type 5, 64 KB pages, allocator field 0 and 256-byte alignment. Bink-style 97- and 193-byte blocks are 32-byte aligned and zeroed, and each wrapper recovers its base. AMD-rounded requests of 256 to 65,792 bytes start a committed read-write region on a 64 KiB boundary and are zeroed and writable. After release, `VirtualQuery` reports the region as free. Nonzero object-type and allocator tags leave heap placement unchanged. |
| `era_heap_concurrency` | Four threads run 100 rounds each. They hold simultaneous allocations, write distinct values, compare every byte and release the memory. |
| `era_heap_caller_layout` | Owned desktop storage covers all 32 base residues at eight payload sizes. Each Bink-style wrapper recovers its original base after a payload write and preserves the surrounding guard bytes. Ten AMD-style requests check 256-byte size rounding and the unused tail. These cases establish caller-side arithmetic. The Bink-style storage uses the recorded Bink word. |

`desktop_memory_query` supplies Windows observations for `XMemVirtualQuery` and `XMemGetWorkingSetStatistics`. The test reserves memory, commits and writes 1,024 pages, checks the read-only/writable boundary, then decommits. It verifies residency of a locked page and records working set, peak working set and private commit at three points. Values vary between runs. Scarlett structure layout, units and accounting scope remain unconfirmed. [Windows working sets](https://learn.microsoft.com/en-us/windows/win32/memory/working-set)

`src/era_query.cpp` is a desktop handler for `XMemVirtualQuery`, shaped by the four BVH call sites. It writes the Win32 `MEMORY_BASIC_INFORMATION` to bytes `0x00`–`0x2F`; the callers read State at `+0x20` and Protect at `+0x24`, matching that layout. The byte at `+0x30` has no public definition: the Microsoft reference for `XMemVirtualQuery` and its result structure is restricted to the Xbox developer program.

The handler therefore fills that byte by a desktop policy, not by a confirmed Scarlett field:

- it writes `0x01` when the queried address lies in a region registered with `register_gpu_region`, and `0x00` otherwise;
- later bytes are left untouched;
- the policy applies only to queries of the current process;
- a buffer shorter than 48 bytes fails with `ERROR_BAD_LENGTH`, as `VirtualQueryEx` does.

With registration, the callers' predicates accept GPU-intended memory and reject everything else. What the console's tail byte means, and what sets it, remain open. `XMemGetWorkingSetStatistics` gets no handler: the selector `0x20000001` and the output units have no public reference.

| CTest case | Inputs and checks |
|---|---|
| `era_query_fields` | Reserved, committed read/write, read-only and no-access pages, each queried with and without registration. Bytes `0x00`–`0x2F` match a plain `VirtualQueryEx` result byte for byte, and bytes after `+0x30` keep their sentinel. |
| `era_query_tail` | Negative control: before registration a committed read/write page fails the tail check. After registering two pages, the tail byte is set at the first, middle and last byte and clear on the next committed page and on a reserved page. The callers' predicates accept read/write and read-only, reject the source check on no-access, and reject both after decommit. A real handle to the process counts as the current process, and unregistering clears the flag. |
| `era_query_registry` | Null, empty and wrapping ranges are refused. Exact, front, back, containing and contained overlaps are refused, adjacent ranges are accepted, and unregistering needs the exact base. |
| `era_query_short` | A 47-byte buffer returns 0 with `ERROR_BAD_LENGTH` and is left untouched. A 48-byte buffer succeeds without writing the tail byte. A null buffer fails. |

## Offscreen rendering and readback

`offscreen_hardware` selects a high-performance adapter. `offscreen_warp` selects Microsoft's software rasterizer. Both draw the same DXC-compiled triangle to a 64×64 RGBA8 texture. After GPU completion the test reads the texture using its row pitch and compares colors and coverage pixel by pixel. Outputs are `triangle_hardware.bmp` and `triangle_warp.bmp` in the build directory. `ctest --preset dev -V` reports the adapter, driver, debug layer and pixel differences. Error or corruption messages from an available debug layer fail the test.

The original test machine had Windows Graphics Tools installed. A second build in `out/build/agility` used Microsoft Agility SDK 1.620.2. Set `-DOFFSCREEN_AGILITY_620_DIR=<directory containing the x64 SDK DLLs>` to reproduce it. This configuration verifies debug interface activation. [Agility SDK package](https://www.nuget.org/packages/Microsoft.Direct3D.D3D12/1.620.2)

## Mapped DLL draw with a Windows reference

`cpu_native_frame_hardware` and `cpu_native_frame_warp` load the same source-built DLL with `LoadLibraryW` and with `pe_map`. Both paths record TLS before the DLL entry on process attach and detach. The DLL's `guest_draw` export selects the shared two-texture shader fixture and calls the graphics provider through its bound import. The provider calls the host test, which runs the existing offscreen renderer in a child process. The renderer checks every RGBA channel against the CPU reference, and the test compares the two 64×64 captures byte for byte. Withholding the graphics provider must fail mapping before startup or drawing.

```powershell
ctest --preset dev --no-tests=error -R '^cpu_native_frame_(hardware|warp)$' -V
```

On Windows 11 build 26200, the RTX 3060 Ti (driver 621.14) and WARP each matched all 4,096 pixels per load path with zero D3D12 debug warnings or errors.

## Static executable intake

By default, the intake tool locates the Scarlett entry in the sole build's `MicrosoftGame.Config`. It accepts the layout path or a separate candidate file and opens the game files for reading only.

```powershell
.\out\pe-env\Scripts\python.exe -B tools/intake_exe.py
# Inspect a separately supplied candidate.
.\out\pe-env\Scripts\python.exe -B tools/intake_exe.py "E:\incoming\game_scarlett_master.exe"
```

Each run writes a new JSON report in `out/intake/`. Exit codes are 3 for a missing layout executable, 2 for an input or inspection failure and 0 for completed inspection. The report links the previous verification time, torrent SHA-256, install chunks and baseline summary. The inspected torrent omitted the EXE. The 13,345 matching pieces and 223,880,932,870 bytes verified in the historical report cover that original payload. A later EXE needs separate provenance. Intake rescans the package PEs and lists changed DLLs.

For each candidate the tool records SHA-256, architecture, entry RVA and section, TLS directory and callback order, then compares imports, modules and unresolved items with the baseline. Source-built PE fixtures and a miniature torrent cover named, ordinal and delayed imports, import names that pefile rejects, missing providers, TLS callbacks, invalid callback addresses, truncation, an absent entry and path conflicts. These checks establish static properties. Authenticity and startup remain unverified.

`pe.startup.locations` lists TLS callbacks in table order followed by the executable entry. Each location includes its RVA, section and file offset. When the x64 exception directory supplies a covering function interval, the report includes its inclusive start, exclusive end and unwind data RVA. A location can legitimately lack a covering record. An absent or unusable table leaves the independently observed startup locations available. In the local debug fixture the table had 110 records, one covering its TLS callback. The entry had no covering record. CTest cross-checks against pefile and exercises both absent and incomplete directories. [Microsoft x64 unwind data](https://learn.microsoft.com/en-us/cpp/build/exception-handling-x64)

### Grouped allocation descriptor builders

In the inspected Bink image, the function interval beginning at RVA `0x67c0` contains direct calls to the group initializer (`0x25b80`), append helper (`0x25b20`) and grouped allocator (`0x25a40`). The initializer places two adjacent arrays after a 64-byte header. Each successful append records a destination and a padded stride. With five available slots, requests of 0, 33, 1, 1025 and 32 bytes produced strides of 32, 64, 128, 1280 and 512 bytes in order. The stride in 32-byte units is congruent to the previous total in units plus one, modulo 32. All five stored strides were even. These helpers do not establish who produces odd tagged entries.

Build `cpu_bink_descriptors_lifted` and run `ctest --preset dev --no-tests=error -R '^cpu_bink_descriptors$'`. The test compares a source-written MASM implementation and its translation against explicit expected arrays after every append. Set `BINK_DESCRIPTOR_IMAGE` to the local Bink DLL to enable the fingerprint-checked `cpu_bink_package_descriptors` target and test. In the translated interface a capacity breakpoint returns status zero, allowing the test to check that both arrays and their guard word survive an extra append. The native control runs only within capacity.

### Bink allocation and release on the Windows heap

The local test lowers the allocation and release callers and the exported `BinkSetMemory` setter from x64 machine code to C++, links the result to a Windows process-heap adapter, and runs the new DLL. The translator follows the setter's export jump and binds its two pointer stores to the same slots the allocation path reads. It also resolves the named PE imports, including tail transfers through linker thunks. The console-specific meaning of the heap attributes remains open.

On Windows, the public native fixture, its translation and the locally translated callers passed the same checks:

- Lengths 0 and `0xffffffff` returned null before reaching the allocator. Three forced allocation failures returned null after one request each. The largest input, `0x100000001`, verified 64-bit size handling without a large allocation.
- Each accepted length requested 64 extra bytes with attributes `0x05e50000`. Returning heap-backed storage at each of the 32 possible base residues produced a 32-byte-aligned user pointer. The check wrote the entire payload and compared both header bytes and the surrounding guards.
- With 32 allocations live, releasing them in reverse order passed each original base to the adapter once with the allocation attributes. All 32 backing blocks were freed by `HeapFree`. Releasing null made no allocator call.
- Callback pairs A and B each received one allocation request (97 and 193 bytes for 33- and 129-byte payloads). Both blocks contained the corresponding release function address, and the payload and guards matched. After installing B, releasing A's block called A's release function once with A's original base. After clearing the callbacks, releasing B's block likewise called B once with B's base. Neither operation called the XMem adapter.
- A null custom return for a 65-byte payload led to exactly one 129-byte `XMemAlloc` request. After switching callbacks, that block went back to `XMemFree` once with the original base and attributes. When both allocation attempts failed the caller returned null. A separate all-ones custom return stopped the request before the XMem adapter.

For the public control, build `cpu_bink_heap_lifted` in the dev preset, then run `ctest --preset dev -R '^cpu_aot_bink_heap$' --no-tests=error -V`. For a local package, set the CMake cache entries `BINK_HEAP_IMAGE` to the DLL path and `BINK_HEAP_ENTRIES` to the semicolon-separated allocation and release function **entry** RVAs for that image. Build `cpu_bink_package_lifted` and run `cpu_aot_bink_package_heap`. Generated translations stay in the build directory.

The grouped caller at RVA `0x25a40` in the inspected Bink DLL tail-calls the allocator at `0x25bf0` when its first argument is null. A 33-byte prefix becomes 64 bytes before the allocator adds its own 64 bytes. With a group and 48 extra bytes, the same input produces a 176-byte request at the heap provider. On success, an odd length word selects a pointer slot relative to the returned block and an even word selects an external slot. Clearing the low bit gives the payload stride. On allocation failure both the extra-byte total and the entry count are cleared and the external slot retains its value. The [descriptor builders](#grouped-allocation-descriptor-builders) produce even strides. The producer of odd tagged strides remains open.

Build `cpu_bink_heap_lifted` and run `ctest --preset dev --no-tests=error -R '^cpu_aot_bink_group$'` for the source-built assembly control and its translation. For the inspected local DLL (SHA-256 `9eaf045d004cd7d6a8382aaff0c6ea23a29fec4ef8bfacd726b54f5bc26fafd0`), set `BINK_HEAP_ENTRIES` to `0x25bf0;0x25bc0` and `BINK_GROUP_ENTRY` to `0x25a40` alongside `BINK_HEAP_IMAGE`. Build `cpu_bink_package_lifted` and run `cpu_aot_bink_package_group`. The test supplies both descriptor arrays and runs the translated caller with the Windows test heap.

## Six API call sites

After loading the toolchain, inspect three local DLLs. `out/calls/six-apis.json` records image SHA-256, import slots, callers of thunks, embedded container sections and code references. Addresses below are RVAs in the relevant DLL. Indirect calls need execution evidence to identify the selected path.

```powershell
.\tools\dev.ps1
.\out\pe-env\Scripts\python.exe -B tools/call_evidence.py "game-files/<build-directory>"
```

| Import | Observed inputs, return handling and desktop check |
|---|---|
| `XMemAlloc` | At `0x25c35`, Bink's fallback requests the length plus 64 with attributes `0x05e50000` and checks for null. It advances the base by `64 - (base & 31)` bytes (33–64), giving a 32-byte-aligned user pointer. The two preceding bytes hold the offset at `[-1]` and a tag at `[-2]`. At `0x565d`, AMD rounds the nonzero request up to a multiple of 256, reads attributes at `0x6eee0` (on-disk initial value `0xa8004000`) and checks for null. Under the ERA layout the alignment fields give 32 and 256 bytes, matching this caller arithmetic. `era_heap_observed_words` checks the desktop handling of both words; allocator-ID and GPU-visibility semantics remain unconfirmed. |
| `XMemFree` | At `0x25be4`, Bink uses the saved offset to recover the allocation base and passes the same attributes. Custom allocations use a matching callback. AMD cleanup at `0x5712` and release at `0x5921` both use the allocation's attribute slot. The latter also clears the stored pointer. No observed caller consumes the return value. |
| `XMemVirtualQuery` | Four BVH callers pass the current process, target address and a zeroed 64-byte buffer. Each checks a nonzero return, bit `0x1000` of the word at `+0x20` and either low bit of the byte at `+0x30`. Source acceleration structure validation at `0x19c69` also requires the byte at `+0x24` to intersect mask `6`. Destination acceleration structure (`0x19ccb`), index buffer (`0x19d9d`) and vertex buffer (`0x19df9`) use the common checks. Diagnostics describe the whole check. The individual tail bits have no confirmed meaning. |
| `XMemGetWorkingSetStatistics` | Seven AMD calls pass selector `0x20000001` and a local output address. At `0x26b3` and elsewhere a zero return contributes zero to a calculation. Success uses the 64-bit value at `+8`. Code near `0x74d2/75b1` uses a before/after difference to update a counter. The units, scope and selector meaning remain open. |
| `D3D12SerializeVersionedRootSignature` | At `0xc2d0` the BVH code copies a caller's 48-byte descriptor and adds flags `0x03000000` at `+0x28`. At `0xc32f` it passes the descriptor, a blob output pointer and a null error output. Negative HRESULT enters diagnostics. Success uses the blob length and data pointer. A synthetic v1.1 descriptor returned `S_OK` with baseline flags and `E_INVALIDARG` with the two added bits. |
| `D3D12CreateRootSignatureDeserializer` | At `0x4f88`, AMD reads a byte pointer and a 32-bit length from record offsets `+0/+8`, then supplies an interface IID and object output address. A negative HRESULT or null object triggers partial cleanup. Success retrieves the descriptor via the object. Desktop code read the root signature from a complete Microsoft sample shader and returned `E_INVALIDARG` for a synthetic shader without one. |

### Graphics input paths

- The BVH serializer helper has 16 direct calls. At `0xd967–0xd998` the caller builds a version, six parameters and the parameter-array address on the stack. At `0x2151d` another path references container `0x25700` and passes it downstream with length `0x1134`. The DLL contains 52 code-referenced DXBC containers with structurally valid DXIL programs and `RTS0` sections. All 52 root signature headers carry `0x03000000`. The desktop failure case reproduces these flag bits. Their meaning remains unconfirmed.
- AMD registers the deserialization function in a callback table at `0xcea3–0xceaa`. A producer at `0xe9b8/0xe9c3` selects container `0x714f0` of length `0x1910`, copies its descriptor record and calls through the table at `0xebad`. The DLL has 29 structurally complete containers, each with a matching pointer-table entry and `RTS0` and `ACCC` sections. None contains DXIL. These observations support an embedded source. Runtime initialization determines the indirect path.
- Counts cover the inspected DLLs. Scene shaders, archive read order and the graphics path used at startup have no execution record here. An entry executable would allow its imports and initialization calls to be examined.

### Microsoft sample on desktop

The test uses Microsoft's SimpleTriangle at `Xbox-GDK-Samples` commit `b1e77b6ac10f95b07296025fda171ca2ddc42dbe`. The project includes a `Gaming.Xbox.Scarlett.x64` configuration and a published reference image. Desktop builds compile the two HLSL files using the sample's three vertices, colors, sRGB render target and clear color. The test checks SHA-256 for both shaders and the image. Set `SIMPLE_TRIANGLE_DIR` to that revision's sample directory and run `ctest --preset dev -R reference_triangle -V`. A sparse checkout into the default location needs no cache entry: `git clone --filter=blob:none --no-checkout https://github.com/microsoft/Xbox-GDK-Samples out/reference/b1e77b6ac10f95b07296025fda171ca2ddc42dbe`, then in that directory `git sparse-checkout set --no-cone /Samples/IntroGraphics/SimpleTriangle/` and `git checkout b1e77b6ac10f95b07296025fda171ca2ddc42dbe`. The sample repository's `.gitattributes` writes `*.hlsl` with CRLF. The test hashes the shaders with LF line endings, as they are stored in the repository, so a raw download and a checkout give the same result. [Microsoft sample overview](https://learn.microsoft.com/en-us/samples/microsoft/xbox-gdk-samples/simpletriangle/) · [Pinned source and reference image](https://github.com/microsoft/Xbox-GDK-Samples/tree/b1e77b6ac10f95b07296025fda171ca2ddc42dbe/Samples/IntroGraphics/SimpleTriangle)

RTX 2060 and WARP rendered at 64×64. Excluding triangle edges by normalized coordinates left 10,812 color channels per adapter. Differences from the source-derived colors were at most 2 on an 8-bit scale. Against the published image the mean absolute difference was 6.88 and the maximum was 17. The two desktop images differed at 94 pixels, by at most 1 per channel. The published image came without a device model, build hash or lossless GPU readback. Source-derived colors supply the acceptance values. Microsoft distributes console GDKX separately. [Microsoft developer resources](https://developer.microsoft.com/en-us/games/resources/)

## Compute and ray tracing before executable intake

The inspection reads bytes from two local DLLs. Desktop parsers inspect serialized descriptors and DXIL. GPU tests execute HLSL built from this repository. After building, run:

```powershell
$pkg = (Get-ChildItem game-files -Directory)[0].FullName
.\out\pe-env\Scripts\python.exe -B tools/probe_roots.py $pkg --controls out/build/dev
ctest --preset dev -R 'compute_|dxr_query|shader_metadata|bvh_buffer_ops' -V
```

`out/calls/desktop-roots.json` records per-container return codes, root signature summaries, matching desktop fixtures, static call counts and validator diagnostics. It also hashes the input inventory and probe executable. Each container was checked independently.

| Windows check | XboxBvhBuilder | AMD upscaler |
|---|---|---|
| Both D3D12 deserializers read the embedded signature from the full container | 52/52 accepted, 16 distinct signatures | 29/29 accepted, 3 distinct signatures |
| Reserialize unmodified v1.1 descriptor | 52/52 returned `E_INVALIDARG` | 29/29 accepted |
| Clear only `0x03000000` in a memory copy and reserialize | 52/52 accepted. Console flag semantics are unknown. | Original flags were zero. |
| Validate the entire container and the internal bitcode separately using the Windows SDK | Both checks rejected all 52 Shader Model 6.5 compute programs. | `ACCC` present, no DXIL input. |

Both source-built control shaders passed container and module validation. For the BVH modules the validator reported an unrecognized `dxil-ms-xb2` target and `xbox.compiler.options` metadata. DXC disassembly showed 43 modules directly calling 33 distinct `xbox.` functions, including cache-controlled reads, cross-lane operations and floating-point atomics. The other nine had no such direct calls. Diagnostics retain up to 2 KiB per message. Counts use the complete disassembly. [DXC validator interface](https://github.com/microsoft/DirectXShaderCompiler/blob/main/include/dxc/dxcapi.h)

The first synthetic compute layout uses an SRV, a UAV and 16 root constants. Its `RTS0` payload matches 11 BVH containers byte for byte after clearing the two flags. The second uses 13 UAVs, 21 SRVs, four root CBVs and a static sampler. It matches 27 unmodified AMD containers. Both tests access the last registers in the descriptor tables. RTX 2060 and WARP wrote 257 correct integer words per layout and preserved 63 sentinel words.

The native DXR fixture builds a bottom-level structure (BLAS) from one triangle and a top-level structure (TLAS) from two instances. Five inline Shader Model 6.5 queries cover hits, a geometric miss, distance clipping, translation and instance masks. Both backends hit instances 7 and 11 at distance 1 and missed the other three rays. Integration of the game's acceleration structure layout and consumers with desktop DXR remains unverified. [Microsoft DXR specification](https://microsoft.github.io/DirectX-Specs/d3d/Raytracing.html)

Four priority BVH containers use the first layout, are referenced by the function starting at `0x20270` and use 32×1×1 thread groups. Source-built HLSL reproduces the data operations inferred from static instructions. Independent CPU results supply the expected values.

| Container RVA | Observed input and write behavior |
|---|---|
| `0xb3ae0` | Copy the specified count of 16-byte input blocks to matching output offsets. |
| `0xb82e0` | Write the same constant to all four 32-bit words of each valid block. |
| `0xb4bd0` | Write consecutive unsigned 32-bit integers starting at a seed. |
| `0xb5be0` | Read the length parameter from the second input word and copy `64 × (parameter + 1)` bytes in steps. When reset is enabled, write the positive and negative maximum finite float bit patterns to six words at output offsets 16–39. |

Tests use disjoint buffers and cover zero length, thread-group boundaries, integer wraparound and untouched memory. Each desktop backend runs 26 cases and checks 384 words per case. The fourth operation schedules its optional reset in a second dispatch with a UAV barrier. Source cross-group synchronization, aliasing and actual game call parameters remain open.

### Module-only validation by DXC release

`shader_metadata_probe` validates each control shader twice through `root_probe`: the whole container with default flags, then the bare bitcode with `DxcValidatorFlags_ModuleOnly`. With the DXC 1.7.2308 in the original Windows SDK 10.0.26100 release, the second call returns `E_INVALIDARG` without a validation status. [Issue #98](https://github.com/R0Y00/Xbox2Windows/issues/98) The test still runs the root signature, container and disassembly checks. It reports a skip only when both module calls return that code and `dxil.dll` has a 101.x file version. `shader_metadata_controls` checks that rule without a validator and prints each case. A refusal from a 1.x validator, one refused and one validated shader in either order, another call failure and a failed module status are all rejected. The Linux Python job runs the same controls.

The table repeats `root_probe`'s two calls with the `dxcompiler.dll` and `dxil.dll` of each official x64 release. The inputs were `compute_root`, `compute_tables` and `triangle_vs` compiled by DXC 1.7.2308. Every release accepted the three containers. The calls ran under Wine 11.0. The 1.7.2308 and 1.9.2609 rows match the native Windows 10 results in #98. `IDxcVersionInfo` reports 1.8 on both sides of the boundary, so the test reads the file version instead.

| DXC release | `dxil.dll` file version | `IDxcVersionInfo` | Module-only call |
|---|---|---|---|
| 1.7.2308 | 101.7.2308.12 | 1.7 | `E_INVALIDARG` |
| 1.8.2403, 1.8.2405, 1.8.2407 | 101.8.2403.18, 101.8.2405.17, 101.8.2407.12 | 1.8 | `E_INVALIDARG` |
| 1.8.2502 | 1.8.2502.8 | 1.8 | Status 0 |
| 1.8.2505, 1.8.2505.1, 1.9.2602, 1.9.2602.24, 1.9.2607, 1.9.2609 | 1.x | 1.9 | Status 0 |

To check another release on Windows, configure a separate build directory with its compiler, then run the test:

```powershell
cmake --preset dev -B out/build/dxc-check -DDXC_EXECUTABLE=<path>\bin\x64\dxc.exe
cmake --build out/build/dxc-check
ctest --test-dir out/build/dxc-check --no-tests=error -R '^shader_metadata_probe$' -V
```

### BVH reads and cross-lane operations

In `out/calls/desktop-roots.json`, `static_operands` from `tools/probe_roots.py` groups functions by module count, call site and operand form. The counts below describe static instructions across the 52 BVH programs. Runtime frequency is unknown.

| Read form | Modules | Static calls |
|---|---:|---:|
| Global, one word | 17 | 227 |
| Global, two words | 3 | 129 |
| Global, three words | 12 | 184 |
| Global, four words | 10 | 44 |
| Scalar, one word | 16 | 32 |

Across 27 distinct modules, all 616 reads use 64-bit addresses and end with two LLVM `undef` arguments. They provide no definite cache policy. At RVA `0xa4780` a count read from base plus 12 limits active threads. A loop in `0xc6260` reads a word at base plus four times the index and writes it to the destination. The fixture exercises both data behaviors and the scalar offset `(index << 8) | 44`.

Two successive writes to the same GPU buffer are each ordered by a UAV-to-SRV transition before reads of all four widths. Each round submits 128 synthetic requests covering low-word carry, shared and scattered addresses, a final valid vector, overrun, misalignment and different high words. The CPU uses independent 64-bit range arithmetic and compares each returned word. Out-of-range sentinels are a fixture rule. Console cache hit rate, address ownership, invalid access behavior and visibility without explicit ordering remain unknown.

Eight modules contain 75 calls to `LaneSwizzle.i32`, with immediates 30, 60, 120, 240 and 480 occurring 15 times each. The `0x95e20` sequence conditionally accumulates exchanged values and reads lane 31. This motivated a candidate five-stage prefix sum within each 32-lane subgroup. Tests exercise native waves and group-shared memory separately. The logical 64-lane path also checks both halves of the ballot mask, prefix counts, dynamic indices and lane 31. The native path checks the active minimum, broadcast, divergent votes and dynamic reads. The CPU reconstructs expectations from GPU-reported native lane IDs.

Each backend runs 38 groups with empty, full and sparse ballots and 32 single-point inputs covering every position in each half. RTX 2060 reported native width 32–32 and WARP 4–4. Both passed the group-shared 64-logical-lane test and skipped forced native wave64 on capability grounds. A capable adapter will run that case. Single-point inputs, CPU prefix sums and final ray hits provide the PC acceptance checks. [WaveReadLaneAt semantics](https://learn.microsoft.com/en-us/windows/win32/direct3dhlsl/wavereadlaneat)

```powershell
ctest --preset dev -R 'bvh_reads|bvh_waves|bvh_wave64|bvh_hits' -V
```

### From buffer reads to ray hits

`bvh_hits_hardware` and `bvh_hits_warp` connect bounded address reads and half-group prefix sums to ray intersections. Box and triangle tests follow AMD GPURT's software approach. A synthetic root holds 64 child bounds with logical 64-bit triangle addresses. The 64 logical lanes test bounds and compact candidates using both half-group sums. Receiving lanes fetch vertices and reduce nearest hits within and across native waves. The fixture breaks distance ties by the smallest primitive ID. [AMD GPURT intersections](https://github.com/GPUOpen-Drivers/gpurt/blob/dev/src/shaders/IntersectCommon.hlsl)

Three successive scenes occupy the same GPU buffer. Each is written as a UAV and then read as an SRV. A CPU brute-force, double-precision plane intersection independently checks each ray's box count, triangle hit count, closest distance and primitive ID. Each round uses the same 132 rays, covering each leaf in isolation, forward, reverse and oblique rays, edges, vertices, zero distance and box-only intersections. Two rays lie in the first row's plane with a zero depth component. They enter the flat boxes through the zero-direction slab branch, one ending exactly on a box face, and never hit a triangle.

| Scene | Hits | Misses |
|---|---:|---:|
| Sparse layout, isolate all 64 leaves | 112 | 20 |
| Permuted addresses, overlapping geometry and equal-distance hits | 14 | 118 |
| Permuted again, with invalid address high bits, an out-of-range tail and a degenerate triangle | 108 | 24 |

RTX 2060 and WARP agreed with the CPU on every field. Closed intervals, tie breaking and invalid-leaf handling are fixture rules. The tests establish synchronized desktop reads and hit results. Scarlett node layouts and cache hints remain hypotheses to check against producer writes and consumer reads.

### Independent BVH checks on PC

Each scene sends the same 136 rays through a double-precision brute-force CPU test, a custom shader reading packed nodes, and standard DXR. DXR builds its own BLAS and TLAS from the original accessible vertices and runs inline queries. All paths check hit status and closest distance. The CPU also checks that the specific DXR-selected primitive intersects at that distance, allowing valid ties. Comparisons use triangle interiors and distance margins. Edge and endpoint cases use the preceding fixture's fixed conventions. [DXR ray extents](https://microsoft.github.io/DirectX-Specs/d3d/Raytracing.html#ray-extents)

RTX 2060 and WARP each completed 408 comparisons. In fault controls only the custom shader's input changes. Swapping child 0 and 32 addresses changed four results, replaying the previous scene changed 102 and aliasing invalid address high bits changed one. Each fault must alter its designated witness ray. Run `ctest --preset dev -R bvh_differential -V` for hit counts, changed results and witness indices.

Project acceptance uses reproducible PC behavior. The next game-specific check is to identify the builder selected by the main executable, its node-buffer inputs and outputs, and the downstream queries, then add the observed constraints to PC tests.

### Building bounds under contention

Static disassembly identified 51 direct calls each to `xbox.global_atomic_fmin` and `xbox.global_atomic_fmax` across seven modules each. Each call takes a 64-bit address and a float after the opcode. Integer atomic add and subtract each occurred at four sites with a 64-bit address and a 32-bit operand. Regenerate `out/calls/desktop-roots.json` with `probe_roots.py` to inspect per-module counts and operand forms.

`bvh_atomics` builds seven sets of axis-aligned bounds in GPU memory. Contending threads use 32-bit compare-and-exchange to extend six raw float fields. Two counters cross the unsigned wrap boundary. The CPU checks every field and verifies that each accepted point received a unique prior value from each counter. Inputs cover positive-only and negative-only bounds, both signed zeros, subnormals, finite extremes, invalid points and an empty bucket. The fixture skips a point with any non-finite coordinate and orders negative zero before positive zero. These are explicit desktop conventions.

The query dispatch reads the accumulated bounds after a UAV barrier and tests 54 axis-aligned rays. The CPU computes each expected bucket mask by projecting the original points onto the three axes. RTX 2060 and WARP each passed 270 ray masks over five input batches, including the empty input and a reversed 1,025-point submission. Each full submission accepted 897 points. This exercises a source-built producer and consumer with shared bounds. The observed atomic calls alone do not identify the game's node layout or special-value rules.

### Packed leaves and initialization stores

The per-module `validation.ir.packing` report follows the immediate definition of the byte selector at all 106 `V_ALIGNBYTE_B32` sites. Each selector is masked with 3. The three modules contribute 44, 44 and 18 sites. Across seven other modules, 47 `BFI` sites include variable masks, zero and contiguous low-bit masks through 1023. The desktop test uses these constraints and AMD's bit selection and byte alignment operations to build a source-owned leaf format. Its field offsets and flag assignments are fixture conventions. [AMD RDNA 2 ISA reference](https://www.amd.com/content/dam/amd/en/documents/radeon-tech-docs/instruction-set-architectures/rdna2-shader-instruction-set-architecture.pdf)

`bvh_packed` uploads 64 packed leaf records per scene. A compute shader reconstructs their fields at four byte offsets, selects the ID and flags through a mask, then writes bounds, triangle vertices and logical addresses. The next dispatch traces the existing 132 rays directly against that buffer. A separate readback compares all 1,280 scene words plus four guard words per scene against the CPU layout. Three scenes cover address carry, an invalid high word, a truncated vertex span, degeneracy and ties. Flipping one selected ID bit in the first scene changed exactly four returned IDs. Distances and counts matched the independent geometry reference in both runs on RTX 2060 and WARP.

The static inventory records one scalar, six three-word and nine four-word cached stores. All have two trailing undefined operands. At module RVA `0xb92d0`, two pairs write the largest finite float three times, then its negative three times at the same base plus 12 bytes. `bvh_atomics` now initializes the six bounds on the GPU from dirty storage with this pair of stores. A UAV barrier orders initialization before accumulation. Empty buckets expose incomplete resets, and independent checks cover the two adjacent counters. Cache policy and the game's allocation layout remain unknown.

### Numeric operations on bounds

All 180 observed `Med3_F32` calls had two constant operands. In 156 calls they were the negative and positive largest finite floats. Six used the positive lower bound with float bits `0x0f000000` and the same upper bound. The remaining 18 used zero and `0x3f7ff972`. For ordered values these medians clamp the variable input to the two constants. The operand inventory now includes all 33 distinct direct Xbox intrinsic names.

`bvh_numeric` checks those three clamps at their boundaries, including neighboring float encodings and infinite inputs. It also tests three-input float extrema, three-input unsigned min/median/max and two independent packed binary16 lanes. For half precision the CPU decodes the exponent and fraction in double precision. Inputs cover every finite nonzero binary16 encoding and positive zero. The fixture maps signed zeros to positive zero and excludes non-finite half inputs. Each desktop backend agreed with the CPU on all 655,360 words from 65,536 input rows. NaN propagation and native Scarlett behavior remain open.

### Updating a live DXR scene

`dxr_update` moves a triangle from depth 1 to 3, changes two instance transforms and changes one visibility mask. It updates BLAS and TLAS in place using separate build and update scratch-size requirements. Seven rays cover old and new positions, visibility masks and distance clipping. Four change hit status. Both desktop backends matched the expected hit, instance, primitive and distance fields before and after the change. Each in-place result matched a fresh full build of the changed scene. [DirectX acceleration structure updates](https://microsoft.github.io/DirectX-Specs/d3d/Raytracing.html#acceleration-structure-update-constraints)

```powershell
ctest --preset dev -R 'bvh_packed|bvh_atomics|bvh_numeric|dxr_update|era_heap_caller_layout|static_exe_intake' -V
```

### Face culling under mirrored instances

`dxr_facing` sends 84 inline queries at seven instances of one two-triangle BLAS. Each instance receives twelve rays: both triangles, both directions along world z, and ray flags `RAY_FLAG_NONE`, `RAY_FLAG_CULL_BACK_FACING_TRIANGLES` and `RAY_FLAG_CULL_FRONT_FACING_TRIANGLES`. The instances combine `D3D12_RAYTRACING_INSTANCE_FLAG_TRIANGLE_FRONT_COUNTERCLOCKWISE`, `D3D12_RAYTRACING_INSTANCE_FLAG_TRIANGLE_CULL_DISABLE`, non-uniform scale and mirroring on x, y or z. The DXR specification defines a triangle as front facing when its vertices appear clockwise from the ray origin in a left-handed frame, evaluates that winding in object space so a negative-determinant instance transform leaves facing unchanged, and makes both cull flags inert on a cull-disabled instance. [Ray flags](https://microsoft.github.io/DirectX-Specs/d3d/Raytracing.html#ray-flags) · [Instance flags](https://microsoft.github.io/DirectX-Specs/d3d/Raytracing.html#d3d12_raytracing_instance_flags)

The CPU reference in `tests/dxr_facing_reference.h` moves each ray into object space, intersects every triangle in double precision and applies those three rules. Before any GPU work the test compares it with a hand-derived table of front and back letters per instance. The reference expects 64 hits, 32 of them front facing, and 20 culled rays. A world-space winding test would disagree on 48 of the 84 rays, which all target the four mirrored instances. For each ray the shader reports hit status, `CommittedTriangleFrontFace`, instance ID, primitive index and distance. Status, facing, instance and primitive must match exactly. A hit distance of 2 or 3 may differ by 1e-5 relative, and a miss must leave all four words zero. Rays cross triangle interiors inside the distance interval, so edge and endpoint rules stay out of scope. The custom BVH shader has no facing rule and takes no part. The fixture skips below DXR tier 1.1 or Shader Model 6.5.

```powershell
ctest --preset dev --no-tests=error -R '^dxr_facing_' -V
```

### Desktop checks for the query tail and root flags

On x64 Windows, `desktop_memory_query` observed a 48-byte write from `VirtualQueryEx`. State and protection occupy offsets `0x20` and `0x24`. All 16 trailing sentinel bytes survived. With the caller's zero initialization, even a committed read/write page fails the tail check. The test supplies seven tail bytes after the query and compares the four callers' two predicate shapes on reserved, committed read/write, read-only and no-access pages. It also checks the zero-return path.

`bvh_root_roundtrip` uses a source-built compute shader with a v1.1 root signature. The desktop serializer rejected the added `0x03000000` bits with `E_INVALIDARG`. Clearing just those bits and serializing, decoding and serializing again produced the same bytes as the original control. RTX 2060 and WARP both created the root signature and pipeline from the control shader and recovered signature. Each dispatch matched 257 CPU reference words and preserved 63 guard words. This establishes the desktop behavior for that shader. The two flags' Scarlett semantics remain open.

`desktop_bvh_root_flags` asserts the same three cases with a synthetic v1.1 descriptor and no local inputs: baseline flags serialize, while `0x01000000`, `0x02000000` and the pair each return `E_INVALIDARG` with no blob produced. The suite therefore fails if a future SDK or driver starts accepting either console bit on its own.

### Creating root signatures on a second workstation

The second maintainer workstation ran Windows 26200.9974 with SDK 10.0.26100.0, an RTX 4060 Laptop GPU (driver 32.0.16.1656) and WARP (10.0.26100.9818). At commit `127fa86`, both backends passed 408 differential ray queries with the debug layer enabled. Forced native wave64 skipped on both. The hardware reported 32 native lanes and WARP reported 4. [Hardware report](https://github.com/R0Y00/Xbox2Windows/issues/1#issuecomment-7058907095)

The device probe groups containers in each DLL by the SHA-256 of their serialized root signature (`RTS0`). For one representative per group it deserializes the v1.1 descriptor, changes the flags in a memory copy, and records the serializer HRESULT. For each successful serialization it calls `CreateRootSignature` on each selected device and records that HRESULT separately.

| Local input | Containers / distinct layouts | Serializer and device results |
|---|---:|---|
| BVH builder | 52 / 16 | All 16 returned `E_INVALIDARG` in each of three cases: original flags, clear only `0x01000000`, or clear only `0x02000000`. Clearing both returned `S_OK` for serialization and device creation on both backends for every layout. |
| AMD upscaler | 29 / 3 | All three original layouts returned `S_OK` for serialization and device creation on both backends. |

The observed boundary is root signature creation. The desktop DXIL validator still rejected all 52 BVH programs, and the 29 upscaler containers contained no DXIL. An open binding test is the BVH layout shared by seven containers, including the one at RVA `0x99d60`. A source-built dispatch with CPU-checked readback would extend coverage beyond device creation for this layout.

After the first build with the Python dependencies, run the two source-built controls on WARP:

```powershell
ctest --preset dev -R '^root_device_controls_warp$' -V
```

With local inputs, generate the call inventory first. The runner verifies the DLL hashes and writes per-layout HRESULTs to `out/calls/root-devices.json`. Use `--warp-only` for a software-only report in `out/calls/root-devices-warp.json`.

```powershell
.\tools\dev.ps1
$pkg = 'game-files/<build-directory>'
$python = '.\out\pe-env\Scripts\python.exe'
& $python -B tools/call_evidence.py $pkg
& $python -B tools/probe_root_devices.py $pkg
```

### Dynamic dependencies at startup

Five `GetProcAddress` calls were inspected in the local AMD loader. The `0x10000` descriptor selects the packaged upscaler, whose five exports match. `0x20000` and `0x30000` select `amd_fidelityfx_framegeneration_xbox.dll`, absent from the inspected directory and torrent manifest. The loader checks for an already-loaded module before calling `LoadLibraryA`. Whether the main executable requests frame generation remains unknown. [AMD FidelityFX API](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/main/Kits/FidelityFX/api/include/ffx_api.h)

The executable intake report's `dependency_paths` gives an explanatory path from the entry to each reachable dependency, preferring ordinary imports and marking delay imports and confirmed AMD branches. The AMD branch data applies only to a SHA-256-matched loader. New inventories include System32 forwarder chains. Older inventories provide first-hop evidence only. Dynamic lookups and execution order remain open. With DLLs alone, trace from each one:

```powershell
.\out\pe-env\Scripts\python.exe -B tools/dependency_paths.py out/dependencies/pre-exe.json |
    Set-Content out/dependencies/pre-exe-paths.json -Encoding utf8
```

To rehearse intake with the locally built candidate, keep its report in a separate directory:

```powershell
.\out\pe-env\Scripts\python.exe -B tools/intake_exe.py out/build/dev/intake_candidate.exe --reports out/intake-rehearsal
```

The tool reads the candidate as a file and uses the manifest slot as its graph name. The local rehearsal matched the candidate's SHA-256 to the fixture and preserved all 16 baseline DLL hashes. The fixture supplied ordinary imports by name and ordinal from the missing `intake_provider.dll`, a delay import from the missing `intake_delay.dll`, and an ordinary import of `ffxQuery` from the packaged AMD loader. From that loader, descriptor `0x10000` reached the packaged upscaler. Descriptors `0x20000` and `0x30000` reached the absent frame-generation module conditionally. The independent CTest uses a temporary package without that loader and checks that the report omits the frame-generation branch. The game's startup dependencies still require its own entry image.

## Resource chunks and shader formats

### State-linked settings in a script archive

`python -B tools/script_state_probe.py path/to/script.rpf --state <label>` checks aligned zlib pages, section lengths through CHKS, schema and array extents, and parent-to-child attribute ranges, and rejects a state node with more than one label-kind `id` attribute, as the replay does. It emits aggregate counts and at most one matching example. The supported profile has five root arrays. Other layouts and entries outside the recognized zlib framing require separate inspection.

In the local base script archive, 532 children of state nodes carried orbit distance, heading and pitch bounds, blend duration and priority together. Reformatting the stored integers, floats and vectors as decimal strings reproduced all five saved value hashes for each child. This establishes authored camera settings associated with states. Activation conditions, time units, interpolation and native calls still need execution evidence. The source-built regression changes a scalar while preserving its saved hash and separately introduces a parent cycle. Further synthetic trees cover a non-state parent, a missing camera attribute, a root with a parent, a state label that selects nothing, and an integer `id` attribute whose hash equals the label, which does not name a state (only label-kind IDs do, as in the replay). [CodeWalker field-name reference](https://github.com/dexyfex/CodeWalker/blob/master/CodeWalker.Core/GameFiles/MetaTypes/MetaNames.cs)

The Windows path replay locates a record by an anchor state ID. It consumes that record's node and attribute rows in memory. After building with the dev preset, run this command with the local base archive:

```powershell
python -B tools/script_path_replay.py path/to/script.rpf --host out/build/dev/script_path_host.dll --record-with bumper_f motorbike ./bumper_f ../bumper_r ../bumper_f car/bumper_f
```

Bare paths begin at the record's `states` container. A leading `./` starts at the active node and `../` moves to its parent. Each input is supplied by the experiment. The native host walks the stored first-child and sibling links, checks reverse links and selects a unique matching child ID. A second lookup uses parent indices to verify the resulting node. A missing or ambiguous input leaves the test host at its last selection. The local motorbike record yields distinct front and rear camera bounds and restores the original bounds on returning to the front. The runtime event that supplies the state ID remains unresolved.

### Drawing BC7 textures with a recompiled pixel shader

`linearize_4k_s_tail(tile, width, height, tail_level)` reads a single BC7 level from a shared GFX10 4 KiB mip-tail tile. Supply the decoded tile, the requested pixel dimensions and its zero-based position within the tail. Whole-tile storage and per-slot bounds are checked before reading. [AMD mip-tail address calculation](https://github.com/GPUOpen-Drivers/pal/blob/c5e800072a32f68b6ccc4422936d96167c6e0728/src/core/imported/addrlib/src/gfx10/gfx10addrlib.cpp)

To test the published fixed-symbol rANS hypothesis against an isolated BCPack prefix, run `python -B tools/bcpack_probe.py packet.bin --blocks N` with an independently established block count for that stream. A chunk can contain several streams. A header match checks the predicted byte budget and initial state. The probe reports a verified constant lane only when it can consume all symbols and reach the expected terminal state without refill bytes. An empty result leaves the lane models unresolved. This probe produces diagnostics only. [Microsoft BCPack modes](https://learn.microsoft.com/en-us/gaming/gdk/docs/reference/system/dstorage/enums/dstorage_bcpack_mode)

`tools/bc7_tiles.py` converts one padded GFX10 `SW_4K_S` surface of 16-byte BC7 blocks to desktop block rows. The caller supplies the pixel dimensions and the bytes of one mip level. Each 4 KiB tile contains 16 × 16 BC7 blocks. The address equation applies to the standard non-XOR mode. [AMD GFX10 swizzle tables](https://github.com/GPUOpen-Drivers/pal/blob/c5e800072a32f68b6ccc4422936d96167c6e0728/src/core/imported/addrlib/src/gfx10/gfx10SwizzlePattern.h)

The desktop check uploads those rows as `DXGI_FORMAT_BC7_UNORM`, runs the translated copy shader, and checks its floating-point readback against CPU-decoded RGBA values after half-precision export. Its case file starts with `BC7C` and two little-endian 32-bit pixel dimensions, followed by tightly packed BC7 block rows and one reference RGBA8 value per texel. The draw check accepts dimensions divisible by four, up to 4096 on either axis and 4 Mi pixels total.

```powershell
ctest --preset dev -R '^bc7_draw_' -V
.\out\build\dev\offscreen_check.exe hardware --check-bc7 copy.cso base.case capture.rgba
.\out\build\dev\offscreen_check.exe hardware --check-bc7-mip1 copy.cso base.case mip1.case
.\out\build\dev\offscreen_check.exe hardware --check-bc7-tail copy.cso 3 mip3.case mip4.case mip5.case mip6.case mip7.case
```

The tail check uses one logical texture per draw. It uploads all supplied consecutive levels at their original subresource indices and sets the SRV's `MostDetailedMip` to the first supplied level. The command above takes a 64×64 first resident level and tests viewports from 512×512 down to 4×4. Point sampling at the three largest sizes must reproduce magnified pixels from the 64×64 reference. Each smaller viewport must match its own reference. The test uses committed resources and measures sampling correctness.

To check a proposed first read round in the BCPack probe, append `--prefetch-mask MASK`. Set bit *i* for each of the first eight lanes expected to read a byte in that round. The probe uses the proposed lane order to locate the byte and compares it with the last byte emitted by the reverse encoder. A matching first byte tests a single refill prediction. Later refill positions and the assignment of BC7 block fields require separate evidence.

The default header layout is interleaved at an eight-byte stride. Pass `--lane-stride 6` to read eight contiguous six-byte records, as proposed for BC7 BCPack streams in [#15](https://github.com/R0Y00/Xbox2Windows/issues/15). `--scan-lanes` prints each lane's mode, budget and state bytes without claiming a decode. The option changes only how the 48-byte header is read; the constant-lane model, the variable BC7 lane models and an independently known BC7 block remain separate questions.

`tools/bc7_blocks.py` supplies independently known BC7 blocks for [issue #15](https://github.com/R0Y00/Xbox2Windows/issues/15). `layout(mode, partition)` lists the name, first bit and width of every field in a 16-byte block, with bit 0 as the low bit of the first byte. `unpack(block)` and `pack(fields)` convert between a block and those named values, and `decode(block)` returns sixteen RGBA8 texels. Every bit of the eight valid modes belongs to one field, so `pack(unpack(block))` reproduces any such block. `fixture()` builds a 64×128 surface of 512 blocks. Each field value comes from a SHA-256 digest of the mode, case number and field name. It cycles through the modes and covers every partition, rotation and index selection value. The module contains no BCPack logic. It gives a candidate lane-to-field mapping the complete block rows and field values to compare against. [Microsoft BC7 format](https://learn.microsoft.com/en-us/windows/win32/direct3d11/bc7-format)

```powershell
ctest --preset dev --no-tests=error -R '^bc7_block_reference$' -V
python -B tools/bc7_blocks.py out/bc7_fields.case --fields out/bc7_fields.json
```

The test compares the first bit of each field group with offsets transcribed from the published mode layouts, checks that every anchor texel lies in its subset, and decodes three blocks whose texels are worked out by hand in the test comments. It also decodes the mode-6 blocks of the draw fixture, which builds its own reference texels, and pins SHA-256 digests of the generated block rows and texels. When the optional `texture2ddecoder` package is installed, the test additionally compares 16,897 decoded blocks with that decoder. This comparison is skipped otherwise. The command above writes the fixture in the `BC7C` case layout. Drawing that case through `offscreen_check` has not been run and would add a GPU decoder as a further reference.

The optional capture contains verified, top-down RGBA8 pixels converted from the GPU readback. The mip test binds both levels in one texture and renders to a half-size target so implicit derivatives select the lower mip. An original mode-6 fixture varies endpoints by block and indices by texel. The negative control shifts only the lower level's compressed blocks and requires a pixel mismatch. [Microsoft BC7 mode reference](https://learn.microsoft.com/en-us/windows/win32/direct3d11/bc7-format-mode-reference)

### Reading logical ranges and probing structured resources

`tools/paged_resource.py` reads a logical byte range from an open archive and a supplied segment map. Each segment gives a physical offset, stored byte count excluding padding, decoded byte count, and codec. Raw and zlib segments may be mixed. The reader verifies the complete compressed stream and decoded size before returning bytes from it. Reads that touch an unknown codec fail. Segments after it remain independently addressable.

`tools/section_probe.py` samples up to 8 MiB at the start, middle and end of each input. It looks for 16-byte-aligned zlib streams, with stored and decoded sizes capped at 256 KiB. A section record must start and end with the requested tags, and its big-endian section sizes must exactly cover the decoded buffer. Streams cut off by a window or size cap are omitted. Counts establish record boundaries within sampled regions. Interpreting payload fields or validating a checksum inside a section requires its format.

```powershell
python -B tools/section_probe.py --first PSIN --last CHKS "game-files/<build-directory>/levels.rpf"
ctest --preset dev -R resource_range_controls -V
```

### Source-built binding, wave and texture checks

`dual_uav` dispatches through two root UAVs with a count supplied as a root constant. The CPU checks the input, active output and unwritten tail over seven counts. Decreasing the count from 129 to 128 leaves word 128 untouched. The wave tests compare a full-group mirror with a half-local mirror at lanes 31 and 32 and check an inclusive sum across both halves. With native wave64 support they also check a native mirror using the lane assignment returned by the GPU.

`texture_mips` writes an original RGBA8 checker pattern in padded 4×4 Morton tiles, decodes it into the device's copy footprints and point-samples every texel through a texture SRV. It checks all 148 texels of a 13×9 texture and its three smaller mips, plus a guard word after each level. Morton order is the synthetic input convention. Establishing a particular title's texture format and addressing mode requires separate evidence.

```powershell
ctest --preset dev -R 'dual_uav|texture_mips|bvh_waves|bvh_wave64' -V
```

### Optional compressed-record inventory

Run the following after building the native validator. The optional pass scans named records in the shader archive, decodes one raw-DEFLATE stream at a time and writes only aggregate metadata to the resource report. A candidate must fit the file, have matching stored sizes, reach the stream end, match the declared output length and contain a bounded DXBC section table with a DXIL program header. The pass caps stored and decoded spans at 8 MiB and 16 MiB. It records shader stages, section counts, LLVM bitcode framing, unused bytes at stream end and rejection reasons. The native validator samples the first accepted container per stage. These are content-based counts and depend on the recognized record format. [External record-format reference](https://github.com/ItsNotRexed/Leonida/blob/56f023a10a726da20702d4e121656ceda2391d93/tools/shader_carve.py)

```powershell
python -B tools/resource_inventory.py "game-files/<build-directory>" --scene-records --scene-validator out/build/dev/root_probe.exe
ctest --preset dev -R 'compressed_scene_metadata|static_exe_intake|desktop_memory_query|era_heap_failure' -V
```

The synthetic intake test compares the candidate's direct unresolved imports against a DLL baseline and against a second baseline with a peer caller. Each `entry_import_gaps` item records ordinary or delayed binding and whether the same module/symbol pair was already unresolved. Transitive and conditional routes remain in `dependency_paths`. The memory tests check a real one-byte Windows query failure and unsupported heap attribute bits in combination with a supported alignment.

### Raw archive scan

After building, use the source-built pixel shader as a known DXIL control.

```powershell
python -B tools/resource_inventory.py "game-files/<build-directory>" --shader-control out/build/dev/triangle_ps.cso
```

`out/resources/<build-directory>.json` records install chunks, flags, byte counts and missing layout destinations. It records file-header signatures for the build and scans all raw bytes in `appdata0.rpf`, `common.rpf` and `shaders.rpf` for format markers. Candidate DXBC containers are checked for length, section boundaries and DXIL program headers. The synthetic `dxcontainer_part_offsets` regression verifies that part-table entries can be enumerated independently of the physical layout, while rejecting overlapping, out-of-bounds, or duplicate-FourCC part ranges. Both the part map and DXIL scanner reject repeated FourCC tags even when those parts are physically separate; the regression includes a valid framed DXIL control to distinguish duplicate tags from missing shader payloads. It checks structural framing, not shader execution. `sample_streams` covers at most eight qualifying zlib streams per file within the first 4 MiB. Plaintext stays in memory. The sample cannot characterize the rest of the archive. Installation order and runtime I/O need independent checks. [DXIL container format](https://github.com/microsoft/DirectXShaderCompiler/blob/main/docs/DXIL.rst) · [RPF9 observation source](https://gtamodx.com/columns/eXk_fiBUaYta)
