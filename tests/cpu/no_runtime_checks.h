// Force-included into the translated EXE, which links without the CRT. Debug builds pass /RTC1,
// whose checks need CRT hooks; turning them off for this translation unit removes the references.
#pragma runtime_checks("", off)
